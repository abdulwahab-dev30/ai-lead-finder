import ipaddress
import os
import re
import socket
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from urllib.parse import parse_qs, unquote, urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from supabase import create_client, Client

APP_NAME = "AI Lead Finder"
USER_AGENT = "AILeadFinder/1.0 (+https://example.com/contact; business research crawler)"
ROLE_PREFIXES = {
    "info", "hello", "contact", "sales", "business", "partnerships",
    "partners", "support", "office", "enquiries", "inquiries",
    "enquiry", "inquiry", "team", "growth", "marketing", "admin"
}
EMAIL_RE = re.compile(r"(?i)\b[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}\b")
PAGE_HINTS = ("contact", "about", "team", "company", "services", "partnership", "business")
MAX_URLS_PER_BATCH = 3
MAX_PAGES_PER_SITE = 8

app = FastAPI(title=APP_NAME, version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

def env(name: str) -> str:
    return os.getenv(name, "").strip()

def get_supabase() -> Client:
    url, key = env("SUPABASE_URL"), env("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise HTTPException(503, "Supabase server configuration is missing.")
    return create_client(url, key)

def authenticate(authorization: str = Header(default="")):
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Sign in to continue.")
    token = authorization.split(" ", 1)[1].strip()
    url, anon_key = env("SUPABASE_URL"), env("SUPABASE_ANON_KEY")
    if not url or not anon_key:
        raise HTTPException(503, "Supabase Auth is not configured.")
    try:
        response = requests.get(
            f"{url.rstrip('/')}/auth/v1/user",
            headers={"apikey": anon_key, "Authorization": f"Bearer {token}"},
            timeout=8,
        )
        if response.status_code != 200:
            raise HTTPException(401, "Your session is invalid or expired.")
        user = response.json()
        email = (user.get("email") or "").lower()
        allowlist = {x.strip().lower() for x in env("ADMIN_EMAILS").split(",") if x.strip()}
        if not allowlist or email not in allowlist:
            raise HTTPException(403, "This account is not on the admin allowlist.")
        return {"id": user["id"], "email": email}
    except requests.RequestException:
        raise HTTPException(503, "Could not verify your Supabase session.")

def normalize_url(raw: str) -> str:
    value = (raw or "").strip()
    if not value:
        raise ValueError("Website URL is required.")
    if not value.startswith(("https://", "http://")):
        value = "https://" + value
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("Enter a valid http(s) website URL.")
    if parsed.username or parsed.password:
        raise ValueError("URLs containing credentials are not allowed.")
    return parsed._replace(fragment="").geturl()

def public_host(url: str) -> bool:
    try:
        host = urlparse(url).hostname
        if not host or host.lower() in {"localhost", "metadata.google.internal"} or host.endswith(".local"):
            return False
        for item in socket.getaddrinfo(host, None):
            address = ipaddress.ip_address(item[4][0])
            if not address.is_global:
                return False
        return True
    except Exception:
        return False

def robots_permits(url: str, session: requests.Session) -> bool:
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    try:
        response = session.get(robots_url, timeout=5, allow_redirects=True)
        # RFC 9309: a robots.txt 4xx response means the file is unavailable;
        # crawling may proceed. A 5xx/network failure remains fail-closed.
        if 400 <= response.status_code < 500:
            return True
        if response.status_code >= 500 or response.status_code != 200:
            return False
        parser = RobotFileParser()
        parser.parse(response.text.splitlines())
        return parser.can_fetch(USER_AGENT, url)
    except requests.RequestException:
        return False

def role_based(email: str) -> bool:
    local = email.split("@", 1)[0].lower()
    prefix = re.split(r"[._+-]", local)[0]
    return prefix in ROLE_PREFIXES

def fetch_html(url: str, session: requests.Session, original_domain: str):
    if not public_host(url):
        return None
    try:
        response = session.get(url, timeout=6, allow_redirects=True, stream=False)
        if get_domain(response.url) != original_domain or not public_host(response.url):
            return None
        if response.status_code >= 400 or "text/html" not in response.headers.get("content-type", "").lower():
            return None
        if len(response.content) > 1_500_000:
            return None
        return response.text
    except requests.RequestException:
        return None

def domain_of(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")

def extract_emails(soup: BeautifulSoup, raw_html: str = ""):
    """Extract email addresses visibly published in page text, links, attributes and common obfuscations."""
    found = set()
    for anchor in soup.select('a[href^="mailto:"]'):
        candidate = anchor.get("href", "").split(":", 1)[-1].split("?", 1)[0].strip()
        for address in EMAIL_RE.findall(candidate):
            found.add(address.lower())

    # Search rendered text, raw HTML (including footer/script data), and common human-readable obfuscations.
    sources = [soup.get_text(" ", strip=True), raw_html]
    for source in sources:
        normalized = source
        normalized = re.sub(r"(?i)\s*(\[at\]|\(at\)|\s+at\s+)\s*", "@", normalized)
        normalized = re.sub(r"(?i)\s*(\[dot\]|\(dot\)|\s+dot\s+)\s*", ".", normalized)
        for candidate in EMAIL_RE.findall(normalized):
            found.add(candidate.lower())
    return found

def useful_links(soup: BeautifulSoup, base: str, domain: str):
    links = []
    for anchor in soup.find_all("a", href=True):
        href = urljoin(base, anchor["href"].strip())
        parsed = urlparse(href)
        if parsed.scheme not in ("http", "https") or domain_of(href) != domain:
            continue
        label = (anchor.get_text(" ", strip=True) + " " + parsed.path).lower()
        if any(word in label for word in PAGE_HINTS):
            clean = parsed._replace(query="", fragment="").geturl()
            if clean not in links:
                links.append(clean)
    return links[:MAX_PAGES_PER_SITE - 1]

def calculate_score(name: str, text: str, emails: set[str]) -> int:
    content = (name + " " + text).lower()
    signals = {
        "software development": 4, "software agency": 5, "web development": 3,
        "saas": 3, "api integration": 5, "automation": 3, "app development": 3,
        "custom software": 4, "bubble": 5, "quickbooks": 4, "no-code": 3,
        "low-code": 3, "product development": 3
    }
    score = sum(points for phrase, points in signals.items() if phrase in content)
    if emails:
        score += 3
    return min(score, 25)

def scan_website(website: str, country: str, niche: str):
    normalized = normalize_url(website)
    domain = domain_of(normalized)
    if not public_host(normalized):
        raise ValueError("Website resolves to a private, reserved or inaccessible host.")
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "text/html"})
    queue = [normalized]
    visited, found, texts, email_sources = set(), set(), [], []
    robots_denied = 0
    fetch_failed = 0
    company = domain
    # Common official contact routes help when the homepage has no visible email link.
    common_contact_paths = (
        "/contact", "/contact-us", "/about", "/about-us",
        "/company", "/imprint", "/impressum", "/get-in-touch",
        "/contact.html", "/contact/",
    )
    queue.extend(urljoin(normalized.rstrip("/") + "/", path.lstrip("/")) for path in common_contact_paths)
    while queue and len(visited) < MAX_PAGES_PER_SITE:
        current = queue.pop(0)
        if current in visited:
            continue
        if not robots_permits(current, session):
            robots_denied += 1
            continue
        visited.add(current)
        html = fetch_html(current, session, domain)
        if not html:
            fetch_failed += 1
            continue
        soup = BeautifulSoup(html, "html.parser")
        if len(visited) == 1 and soup.title:
            company = soup.title.get_text(" ", strip=True)[:160] or domain
        text = soup.get_text(" ", strip=True)
        texts.append(text[:10000])
        emails = extract_emails(soup, html)
        if emails:
            found.update(emails)
            email_sources.append(current)
        if len(visited) == 1:
            queue.extend(useful_links(soup, current, domain))
            # Prioritize links explicitly labelled contact/about over generic navigation.
            queue = list(dict.fromkeys(queue))
        time.sleep(0.25)
    combined = " ".join(texts)
    print(
        "Website scan diagnostics:",
        domain,
        "pages_checked:", len(visited),
        "pages_with_content:", len(texts),
        "robots_denied:", robots_denied,
        "fetch_failed:", fetch_failed,
        "emails_found:", len(found),
    )
    return {
        "company": company,
        "website": normalized,
        "country": country,
        "niche": niche,
        "business_emails": sorted(found),
        "email_source_pages": email_sources,
        "lead_score": calculate_score(company, combined, found),
        "status": "new",
        "notes": "Publicly listed website contact details. Manually review relevance and legal basis before outreach.",
        "last_scanned_at": datetime.now(timezone.utc).isoformat(),
    }

class DiscoverRequest(BaseModel):
    country: str = Field(default="USA", max_length=40)
    niche: str = Field(default="Software development agencies", max_length=100)
    limit: int = Field(default=8, ge=1, le=8)

def discover_from_openstreetmap(country: str, niche: str, limit: int):
    """Find businesses with publicly mapped websites using free OpenStreetMap data."""
    headers = {
        "User-Agent": "AILeadFinder/1.0 (public business research; contact: admin)",
        "Accept": "application/json",
    }
    session = requests.Session()
    session.headers.update(headers)
    geo = session.get(
        "https://nominatim.openstreetmap.org/search",
        params={"country": country, "format": "jsonv2", "limit": 1},
        timeout=15,
    )
    geo.raise_for_status()
    places = geo.json()
    if not places:
        return []
    place = places[0]
    osm_type = place.get("osm_type")
    osm_id = int(place.get("osm_id", 0))
    area_id = osm_id + (3600000000 if osm_type == "relation" else 2400000000 if osm_type == "way" else 0)
    if not area_id:
        return []

    niche_text = niche.lower()
    tag_filters = {
        "software": ['["office"="it"]', '["craft"="software"]', '["office"="company"]'],
        "it": ['["office"="it"]', '["craft"="software"]'],
        "technology": ['["office"="it"]', '["craft"="software"]'],
        "web development": ['["office"="it"]', '["craft"="software"]'],
        "marketing": ['["office"="advertising"]', '["office"="marketing"]'],
        "digital marketing": ['["office"="advertising"]', '["office"="marketing"]'],
        "restaurant": ['["amenity"="restaurant"]'],
        "cafe": ['["amenity"="cafe"]'],
        "hotel": ['["tourism"="hotel"]', '["tourism"="guest_house"]'],
        "construction": ['["office"="construction_company"]', '["craft"="builder"]'],
        "real estate": ['["office"="estate_agent"]'],
        "medical": ['["amenity"="clinic"]', '["amenity"="doctors"]'],
        "healthcare": ['["amenity"="clinic"]', '["amenity"="doctors"]'],
        "education": ['["amenity"="school"]', '["amenity"="college"]', '["amenity"="university"]'],
        "law": ['["office"="lawyer"]'],
        "accounting": ['["office"="accountant"]'],
        "travel": ['["office"="travel_agent"]'],
    }
    selected = []
    for key, filters in tag_filters.items():
        if key in niche_text:
            selected.extend(filters)
    if selected:
        selectors = "\n".join(f'  nwr{flt}["name"]["website"](area.searchArea);' for flt in dict.fromkeys(selected))
    else:
        selectors = '\n'.join([
            '  nwr["name"]["website"]["office"](area.searchArea);',
            '  nwr["name"]["website"]["shop"](area.searchArea);',
            '  nwr["name"]["website"]["amenity"](area.searchArea);',
            '  nwr["name"]["website"]["craft"](area.searchArea);',
            '  nwr["name"]["website"]["tourism"](area.searchArea);',
        ])
    query = f'[out:json][timeout:25]; area({area_id})->.searchArea; ({selectors}); out center tags 300;'
    endpoints = [
        "https://overpass-api.de/api/interpreter",
        "https://overpass.kumi.systems/api/interpreter",
        "https://overpass.private.coffee/api/interpreter",
    ]
    data = None
    for endpoint in endpoints:
        try:
            response = session.post(endpoint, data={"data": query}, timeout=35)
            if response.ok:
                data = response.json()
                break
        except (requests.RequestException, ValueError):
            continue
    if data is None:
        return []

    results, seen = [], set()
    for element in data.get("elements", []):
        tags = element.get("tags") or {}
        raw_url = tags.get("website") or tags.get("contact:website") or ""
        if not raw_url:
            continue
        if not raw_url.startswith(("http://", "https://")):
            raw_url = "https://" + raw_url
        try:
            parsed = urlparse(raw_url)
            host = (parsed.hostname or "").lower().removeprefix("www.")
            if parsed.scheme not in ("http", "https") or not host or "." not in host or host in seen:
                continue
            if not public_host(raw_url):
                continue
            seen.add(host)
            results.append(raw_url.rstrip("/"))
            if len(results) >= limit:
                break
        except Exception:
            continue
    print("OpenStreetMap discovery:", country, niche, "results:", len(results))
    return results


def discover_websites(country: str, niche: str, limit: int):
    """Discover official business domains using provider-specific result parsers."""
    queries = [
        f"{niche} companies in {country} official website",
        f"top {niche} agencies {country}",
        f"{niche} company {country} contact",
    ]
    excluded = {
        "google.com", "bing.com", "duckduckgo.com", "yahoo.com",
        "facebook.com", "linkedin.com", "instagram.com", "youtube.com",
        "wikipedia.org", "yelp.com", "clutch.co", "crunchbase.com",
        "mapquest.com", "yellowpages.com", "trustpilot.com", "reddit.com",
        "glassdoor.com", "indeed.com", "g2.com", "sortlist.com",
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/rss+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    session = requests.Session()
    session.headers.update(headers)
    found, seen, diagnostics = [], set(), []
    # Free OpenStreetMap discovery is tried first; search engines remain fallbacks.
    try:
        for candidate in discover_from_openstreetmap(country, niche, limit):
            parsed = urlparse(candidate)
            host = (parsed.hostname or "").lower().removeprefix("www.")
            if host and host not in seen:
                seen.add(host)
                found.append(candidate)
    except Exception as exc:
        diagnostics.append(f"OpenStreetMap:{type(exc).__name__}")

    def add_candidate(raw_url: str):
        raw_url = unquote((raw_url or "").strip())
        if raw_url.startswith("//"):
            raw_url = "https:" + raw_url
        parsed = urlparse(raw_url)
        host = (parsed.hostname or "").lower().removeprefix("www.")
        if parsed.scheme not in ("http", "https") or not host or "." not in host:
            return
        if any(host == item or host.endswith("." + item) for item in excluded):
            return
        if parsed.path.lower().endswith((".pdf", ".jpg", ".jpeg", ".png", ".webp")):
            return
        clean_url = parsed._replace(query="", fragment="").geturl().rstrip("/")
        if host not in seen:
            seen.add(host)
            found.append(clean_url)

    for query in queries:
        if len(found) >= limit:
            break
        # Bing's RSS endpoint provides direct result URLs and avoids brittle HTML selectors.
        try:
            response = session.get(
                "https://www.bing.com/search",
                params={"q": query, "format": "rss"},
                timeout=12,
            )
            diagnostics.append(f"BingRSS:{response.status_code}")
            if response.ok and ("<rss" in response.text[:1000].lower() or "<feed" in response.text[:1000].lower()):
                root = ET.fromstring(response.content)
                for item in root.iter():
                    if item.tag.split("}")[-1].lower() not in ("item", "entry"):
                        continue
                    href = ""
                    for child in list(item):
                        if child.tag.split("}")[-1].lower() == "link":
                            href = child.attrib.get("href") or (child.text or "").strip()
                            if href:
                                break
                    add_candidate(href)
                    if len(found) >= limit:
                        break
        except requests.RequestException as exc:
            diagnostics.append(f"BingRSS:{type(exc).__name__}")

    # HTML fallbacks use each engine's own query parameters and result markup.
    html_providers = [
        ("BingHTML", "https://www.bing.com/search", lambda q: {"q": q}),
        ("Yahoo", "https://search.yahoo.com/search", lambda q: {"p": q}),
        ("DuckDuckGo", "https://html.duckduckgo.com/html/", lambda q: {"q": q}),
    ]
    for provider, endpoint, params_for in html_providers:
        if len(found) >= limit:
            break
        provider_count = 0
        for query in queries:
            if len(found) >= limit:
                break
            try:
                response = session.get(endpoint, params=params_for(query), timeout=12)
                diagnostics.append(f"{provider}:{response.status_code}")
                if not response.ok:
                    continue
                soup = BeautifulSoup(response.text, "html.parser")
                if provider == "BingHTML":
                    anchors = soup.select("li.b_algo h2 a[href], #b_results h2 a[href]")
                elif provider == "Yahoo":
                    anchors = soup.select("div.dd.algo h3 a[href], h3 a[href], div.algo a[href]")
                else:
                    anchors = soup.select("a.result__a[href], .result__body a[href]")
                for anchor in anchors:
                    href = anchor.get("href", "")
                    parsed = urlparse(href)
                    host = (parsed.hostname or "").lower()
                    if host.endswith("duckduckgo.com"):
                        href = parse_qs(parsed.query).get("uddg", [""])[0]
                    elif host.endswith("bing.com") and parsed.path.startswith("/ck/"):
                        # Bing's encoded tracking URLs use the u=a1... parameter.
                        token = parse_qs(parsed.query).get("u", [""])[0]
                        if token.startswith("a1"):
                            import base64
                            try:
                                href = base64.b64decode(token[2:] + "===").decode("utf-8", "ignore")
                            except Exception:
                                href = ""
                        else:
                            href = ""
                    before = len(found)
                    add_candidate(href)
                    provider_count += len(found) - before
                    if len(found) >= limit:
                        break
            except requests.RequestException as exc:
                diagnostics.append(f"{provider}:{type(exc).__name__}")
        diagnostics.append(f"{provider}_candidates:{provider_count}")

    print("Business discovery diagnostics:", ", ".join(diagnostics), "results:", len(found))
    return found[:limit]


class EmailDraftRequest(BaseModel):
    company: str = Field(min_length=1, max_length=160)
    website: str = Field(min_length=1, max_length=500)
    country: str = Field(default="", max_length=80)
    niche: str = Field(default="", max_length=120)
    recipient_email: str = Field(default="", max_length=254)
    sender_name: str = Field(default="Your Name", max_length=100)
    service: str = Field(default="React, Bubble.io and web application development", max_length=240)
    tone: str = Field(default="professional and friendly", max_length=80)

@app.post("/api/generate-email")
def generate_email(payload: EmailDraftRequest, user=Depends(authenticate)):
    """Create a tailored, editable cold outreach draft without inventing company facts."""
    company = payload.company.strip()
    website = payload.website.strip()
    service = payload.service.strip()
    subject = f"Development support for {company}"
    body = (
        f"Hi {company} team,\n\n"
        f"I came across {company} while researching {payload.niche or 'technology companies'} "
        f"in {payload.country or 'your market'}. I wanted to introduce myself and see whether "
        f"your team ever works with external development partners.\n\n"
        f"I help businesses with {service}. I can support an existing team with focused delivery, "
        f"feature development, integrations, or short-term project work.\n\n"
        f"If you have any upcoming work where an extra developer could help, would you be open "
        f"to a brief conversation?\n\n"
        f"Best regards,\n{payload.sender_name.strip() or 'Your Name'}"
    )
    # Use OpenAI when a server-side key is configured; keep a safe editable fallback otherwise.
    api_key = env("OPENAI_API_KEY")
    if api_key:
        try:
            response = requests.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": env("OPENAI_MODEL") or "gpt-4o-mini",
                    "temperature": 0.6,
                    "messages": [
                        {"role": "system", "content": "Write concise, respectful B2B cold outreach emails. Never invent facts about the recipient company, claim to have reviewed projects not supplied, or use deceptive familiarity. Return only JSON with subject and body."},
                        {"role": "user", "content": f"Write a personalized but truthful first-contact email. Company: {company}. Website: {website}. Country: {payload.country}. Business type: {payload.niche}. Recipient: {payload.recipient_email or 'general business inbox'}. Sender: {payload.sender_name}. Services offered: {service}. Tone: {payload.tone}. Ask whether they need external development support. JSON only."}
                    ],
                    "response_format": {"type": "json_object"},
                },
                timeout=20,
            )
            response.raise_for_status()
            generated = response.json()["choices"][0]["message"]["content"]
            import json
            parsed = json.loads(generated)
            subject = str(parsed.get("subject") or subject)[:180]
            body = str(parsed.get("body") or body)[:5000]
        except Exception as exc:
            print("AI email generation fallback:", type(exc).__name__)
    return {"subject": subject, "body": body, "ai_generated": bool(api_key and subject != f"Development support for {company}")}

class ScanRequest(BaseModel):
    websites: list[str] = Field(min_length=1, max_length=MAX_URLS_PER_BATCH)
    country: str = Field(default="USA", max_length=40)
    niche: str = Field(default="Software development agencies", max_length=100)

class StatusUpdate(BaseModel):
    status: str = Field(pattern="^(new|reviewed|contacted|replied|interested|not_a_fit)$")

@app.get("/api/health")
def health():
    return {"ok": True, "service": APP_NAME, "batch_limit": MAX_URLS_PER_BATCH}

@app.post("/api/discover")
def discover_and_scan(payload: DiscoverRequest, user=Depends(authenticate)):
    try:
        websites = discover_websites(payload.country, payload.niche, payload.limit)
    except requests.RequestException:
        raise HTTPException(502, "Business discovery is temporarily unavailable. Please try again shortly.")
    if not websites:
        raise HTTPException(502, "Search providers did not return company websites. Please try a different business type or try again later.")
    db = get_supabase()
    results = []
    for website in websites:
        # Always preserve the discovered business, even if its website blocks crawling
        # or no public email address is published.
        base_lead = {
            "company": domain_of(website),
            "website": website,
            "country": payload.country,
            "niche": payload.niche,
            "business_emails": [],
            "email_source_pages": [],
            "lead_score": 0,
            "status": "new",
            "notes": "Business discovered. No public email confirmed yet; review the official website/contact page.",
            "last_scanned_at": datetime.now(timezone.utc).isoformat(),
            "owner_id": user["id"],
        }
        try:
            lead = scan_website(website, payload.country, payload.niche)
            base_lead.update(lead)
        except Exception as exc:
            # Keep the prospect instead of dropping it because of a scan/robots/network error.
            base_lead["notes"] = (
                "Business discovered; website scan could not be completed ("
                + type(exc).__name__
                + "). Review the official website manually."
            )
        try:
            saved = db.table("leads").upsert(
                base_lead, on_conflict="owner_id,website"
            ).execute()
            results.extend(saved.data or [base_lead])
        except Exception as exc:
            results.append({
                "website": website,
                "company": base_lead["company"],
                "business_emails": base_lead["business_emails"],
                "status": "save_error",
                "error": type(exc).__name__,
            })
    return {"results": results, "discovered": len(websites)}

@app.post("/api/scan")
def scan_batch(payload: ScanRequest, user=Depends(authenticate)):
    db = get_supabase()
    results = []
    for website in payload.websites:
        try:
            lead = scan_website(website, payload.country, payload.niche)
            lead["owner_id"] = user["id"]
            saved = db.table("leads").upsert(
                lead, on_conflict="owner_id,website"
            ).execute()
            results.extend(saved.data or [lead])
        except ValueError as exc:
            results.append({"website": website, "status": "error", "error": str(exc)})
        except Exception as exc:
            results.append({"website": website, "status": "error", "error": "Scan or save failed: " + type(exc).__name__})
    return {"results": results}

@app.get("/api/leads")
def list_leads(user=Depends(authenticate)):
    db = get_supabase()
    response = db.table("leads").select("*").eq("owner_id", user["id"]).order("created_at", desc=True).limit(500).execute()
    return {"leads": response.data or []}

@app.patch("/api/leads/{lead_id}")
def update_lead(lead_id: str, payload: StatusUpdate, user=Depends(authenticate)):
    db = get_supabase()
    response = db.table("leads").update({"status": payload.status}).eq("id", lead_id).eq("owner_id", user["id"]).execute()
    if not response.data:
        raise HTTPException(404, "Lead not found.")
    return {"lead": response.data[0]}

@app.delete("/api/leads/{lead_id}")
def delete_lead(lead_id: str, user=Depends(authenticate)):
    db = get_supabase()
    response = db.table("leads").delete().eq("id", lead_id).eq("owner_id", user["id"]).execute()
    return {"deleted": bool(response.data)}

@app.get("/")
def frontend():
    index = os.path.join(os.path.dirname(__file__), "frontend", "dist", "index.html")
    if not os.path.isfile(index):
        return JSONResponse({"message": "Frontend build not found. Run npm build in frontend/."}, status_code=503)
    return FileResponse(index)

@app.get("/assets/{asset_path:path}")
def frontend_assets(asset_path: str):
    path = os.path.join(os.path.dirname(__file__), "frontend", "dist", "assets", asset_path)
    root = os.path.realpath(os.path.join(os.path.dirname(__file__), "frontend", "dist", "assets"))
    resolved = os.path.realpath(path)
    if not resolved.startswith(root + os.sep) or not os.path.isfile(resolved):
        raise HTTPException(404, "Asset not found.")
    return FileResponse(resolved)

@app.get("/assets/{asset_path:path}")
def frontend_assets(asset_path: str):
    assets_root = os.path.realpath(os.path.join(os.path.dirname(__file__), "frontend", "dist", "assets"))
    candidate = os.path.realpath(os.path.join(assets_root, asset_path))
    if not candidate.startswith(assets_root + os.sep) or not os.path.isfile(candidate):
        raise HTTPException(404, "Asset not found.")
    return FileResponse(candidate)

@app.get("/{path:path}")
def frontend_fallback(path: str):
    if path.startswith("api/"):
        raise HTTPException(404, "API route not found.")
    return frontend()

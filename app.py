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
MAX_PAGES_PER_SITE = 4

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
        response = session.get(robots_url, timeout=5, allow_redirects=False)
        if response.status_code == 404:
            return True
        if response.status_code != 200:
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

def extract_emails(soup: BeautifulSoup):
    found = set()
    for anchor in soup.select('a[href^="mailto:"]'):
        candidate = anchor.get("href", "").split(":", 1)[-1].split("?", 1)[0].strip()
        if EMAIL_RE.fullmatch(candidate):
            found.add(candidate.lower())
    text = soup.get_text(" ", strip=True)
    for candidate in EMAIL_RE.findall(text):
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
    company = domain
    while queue and len(visited) < MAX_PAGES_PER_SITE:
        current = queue.pop(0)
        if current in visited or not robots_permits(current, session):
            continue
        visited.add(current)
        html = fetch_html(current, session, domain)
        if not html:
            continue
        soup = BeautifulSoup(html, "html.parser")
        if len(visited) == 1 and soup.title:
            company = soup.title.get_text(" ", strip=True)[:160] or domain
        text = soup.get_text(" ", strip=True)
        texts.append(text[:10000])
        emails = extract_emails(soup)
        if emails:
            found.update(emails)
            email_sources.append(current)
        if len(visited) == 1:
            queue.extend(useful_links(soup, current, domain))
        time.sleep(0.25)
    combined = " ".join(texts)
    return {
        "company": company,
        "website": normalized,
        "country": country,
        "niche": niche,
        "business_emails": sorted(found),
        "email_source_pages": email_sources,
        "lead_score": calculate_score(company, combined, found),
        "status": "new",
        "notes": "Public role-based inbox only. Manually review relevance and legal basis before outreach.",
        "last_scanned_at": datetime.now(timezone.utc).isoformat(),
    }

class DiscoverRequest(BaseModel):
    country: str = Field(default="USA", max_length=40)
    niche: str = Field(default="Software development agencies", max_length=100)
    limit: int = Field(default=8, ge=1, le=8)

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

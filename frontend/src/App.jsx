import { useEffect, useMemo, useState } from "react";
import {
  Activity, ArrowDownToLine, ArrowUpRight, BadgeCheck, Building2, Check,
  ChevronDown, CircleHelp, Command, Copy, Globe2, LayoutDashboard, Loader2,
  LogOut, Mail, Plus, Search, ShieldCheck, SlidersHorizontal, Sparkles, FileText, Send,
  Target, Trash2, TrendingUp, Users, X, Zap
} from "lucide-react";
import { supabase } from "./lib/supabase";

const API_BASE = import.meta.env.VITE_API_BASE || "";
const countries = ["USA", "UK", "Canada", "Australia"];
const niches = [
  "Software development agencies",
  "Bubble.io development agencies",
  "SaaS development companies",
  "API integration agencies",
  "QuickBooks consultants",
  "Web application development studios",
];

function searchHref(niche, country) {
  return `https://www.google.com/search?q=${encodeURIComponent(`${niche} ${country} contact agency`)}`;
}
function initials(name = "") {
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map((x) => x[0]).join("").toUpperCase() || "AL";
}
function scoreLabel(score = 0) {
  if (score >= 14) return "Strong";
  if (score >= 8) return "Good";
  return "Review";
}
function formatDate(value) {
  if (!value) return "—";
  return new Date(value).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}
function App() {
  const [session, setSession] = useState(null);
  const [authReady, setAuthReady] = useState(false);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [authError, setAuthError] = useState("");
  const [busy, setBusy] = useState(false);
  const [leads, setLeads] = useState([]);
  const [loadingLeads, setLoadingLeads] = useState(false);
  const [country, setCountry] = useState("USA");
  const [niche, setNiche] = useState(niches[0]);
  const [urls, setUrls] = useState("");
  const [searching, setSearching] = useState(false);
  const [progress, setProgress] = useState({ done: 0, total: 0, message: "" });
  const [notice, setNotice] = useState("");
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("all");
  const [activeNav, setActiveNav] = useState("Overview");
  const [emailLead, setEmailLead] = useState(null);
  const [draftSubject, setDraftSubject] = useState("");
  const [recipientEmail, setRecipientEmail] = useState("");
  const [draftBody, setDraftBody] = useState("");
  const [senderName, setSenderName] = useState("");
  const [service, setService] = useState("React, Bubble.io, Python and web application development");
  const [draftBusy, setDraftBusy] = useState(false);

  useEffect(() => {
    if (!supabase) { setAuthReady(true); return; }
    supabase.auth.getSession().then(({ data }) => {
      setSession(data.session);
      setAuthReady(true);
    });
    const { data: listener } = supabase.auth.onAuthStateChange((_event, next) => setSession(next));
    return () => listener.subscription.unsubscribe();
  }, []);

  async function api(path, options = {}) {
    if (!session?.access_token) throw new Error("Please sign in again.");
    const response = await fetch(`${API_BASE}/api${path}`, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${session.access_token}`,
        ...(options.headers || {}),
      },
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || data.message || `Request failed (${response.status})`);
    return data;
  }

  async function loadLeads() {
    if (!session) return;
    setLoadingLeads(true);
    try {
      const data = await api("/leads");
      setLeads(data.leads || []);
    } catch (error) { setNotice(error.message); }
    finally { setLoadingLeads(false); }
  }
  useEffect(() => { if (session) loadLeads(); }, [session]);

  async function signIn(event) {
    event.preventDefault();
    if (!supabase) return setAuthError("Add VITE_SUPABASE_URL and VITE_SUPABASE_ANON_KEY in Vercel.");
    setBusy(true); setAuthError("");
    const { error } = await supabase.auth.signInWithPassword({ email, password });
    if (error) setAuthError(error.message);
    setBusy(false);
  }
  async function signOut() { await supabase?.auth.signOut(); setLeads([]); }

  async function runScan(event) {
    event.preventDefault();
    setSearching(true); setNotice("");
    setProgress({ done: 0, total: 0, message: "Discovering businesses for your selected market…" });
    try {
      const data = await api("/discover", {
        method: "POST",
        body: JSON.stringify({ country, niche, limit: 8 }),
      });
      const results = data.results || [];
      const saved = results.filter((item) => item.status !== "error" && item.status !== "save_error").length;
      const withEmails = results.filter((item) => (item.business_emails || []).length > 0).length;
      await loadLeads();
      setProgress({ done: results.length, total: results.length, message: "Discovery finished" });
      setNotice(`Found ${results.length} businesses; ${withEmails} had public business emails. ${saved} saved to your leads.`);
    } catch (error) {
      setNotice(error.message);
    } finally {
      setSearching(false);
    }
  }

  async function createEmailDraft(lead) {
    setEmailLead(lead);
    setDraftSubject("");
    setDraftBody("");
    setRecipientEmail((lead.business_emails || [])[0] || "");
    setDraftBusy(true);
    try {
      const data = await api("/generate-email", {
        method: "POST",
        body: JSON.stringify({
          company: lead.company,
          website: lead.website,
          country: lead.country || "",
          niche: lead.niche || "",
          recipient_email: (lead.business_emails || [])[0] || "",
          sender_name: senderName || "Your Name",
          service,
          tone: "professional and friendly",
        }),
      });
      setDraftSubject(data.subject || "");
      setDraftBody(data.body || "");
    } catch (error) {
      setNotice(error.message);
      setEmailLead(null);
    } finally {
      setDraftBusy(false);
    }
  }
  function openMailClient() {
    if (!emailLead) return;
    const recipient = recipientEmail.trim();
    const href = `mailto:${encodeURIComponent(recipient)}?subject=${encodeURIComponent(draftSubject)}&body=${encodeURIComponent(draftBody)}`;
    window.location.href = href;
    if (emailLead.id) updateStatus(emailLead.id, "contacted");
  }

  async function updateStatus(id, status) {
    try {
      await api(`/leads/${id}`, { method: "PATCH", body: JSON.stringify({ status }) });
      setLeads((old) => old.map((lead) => lead.id === id ? { ...lead, status } : lead));
    } catch (error) { setNotice(error.message); }
  }
  async function deleteLead(id) {
    if (!confirm("Delete this lead?")) return;
    try {
      await api(`/leads/${id}`, { method: "DELETE" });
      setLeads((old) => old.filter((lead) => lead.id !== id));
    } catch (error) { setNotice(error.message); }
  }
  function exportCsv() {
    const headers = ["company", "website", "country", "business_emails", "lead_score", "status", "email_source_pages"];
    const rows = filteredLeads.map((lead) => headers.map((key) => {
      const value = Array.isArray(lead[key]) ? lead[key].join("; ") : (lead[key] ?? "");
      return `"${String(value).replaceAll('"', '""')}"`;
    }).join(","));
    const blob = new Blob([[headers.join(","), ...rows].join("\n")], { type: "text/csv;charset=utf-8" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob); link.download = "ai-lead-finder-leads.csv"; link.click();
    URL.revokeObjectURL(link.href);
  }
  async function copy(text) {
    try { await navigator.clipboard.writeText(text); setNotice("Copied to clipboard."); }
    catch { setNotice("Could not copy. Select the text manually."); }
  }

  const filteredLeads = useMemo(() => leads.filter((lead) => {
    const matchesQuery = `${lead.company} ${lead.website} ${(lead.business_emails || []).join(" ")}`.toLowerCase().includes(query.toLowerCase());
    const matchesFilter = filter === "all" || lead.status === filter;
    return matchesQuery && matchesFilter;
  }), [leads, query, filter]);
  const emailCount = leads.reduce((sum, lead) => sum + (lead.business_emails || []).length, 0);
  const strongCount = leads.filter((lead) => (lead.lead_score || 0) >= 14).length;
  const countriesCount = new Set(leads.map((lead) => lead.country)).size;

  if (!authReady) return <div className="loading-screen"><Loader2 className="spin" /> Loading workspace…</div>;
  if (!session) return (
    <div className="login-shell">
      <div className="login-art">
        <div className="brand"><div className="brand-mark"><Target size={19}/></div><span>Prospect<span className="brand-light">Pilot</span></span></div>
        <div className="login-copy">
          <div className="eyebrow"><Sparkles size={14}/> INTELLIGENT PROSPECTING</div>
          <h1>Find the right<br/><em>business conversations.</em></h1>
          <p>Research software agencies, discover public business contacts, and keep your outreach pipeline organized.</p>
          <div className="login-proof"><div className="avatar-stack"><b>US</b><b>UK</b><b>CA</b><b>AU</b></div><span>Built for focused B2B research</span></div>
        </div>
        <div className="art-grid"><div/><div/><div/><div/><div/><div/><div/><div/><div/></div>
        <div className="login-footer">PRIVATE WORKSPACE · PUBLIC BUSINESS CONTACTS ONLY</div>
      </div>
      <div className="login-panel">
        <form className="login-card" onSubmit={signIn}>
          <div className="mobile-brand"><div className="brand-mark"><Target size={18}/></div> ProspectPilot</div>
          <div className="eyebrow">WELCOME BACK</div>
          <h2>Sign in to your workspace</h2>
          <p className="muted">Use the admin account created in your Supabase project.</p>
          <label>Email address<input type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@company.com" required/></label>
          <label>Password<input type="password" value={password} onChange={(e) => setPassword(e.target.value)} placeholder="Your password" required/></label>
          {authError && <div className="alert error">{authError}</div>}
          <button className="primary full" disabled={busy}>{busy ? <Loader2 className="spin" size={17}/> : "Sign in"} <ArrowUpRight size={16}/></button>
          <div className="secure-note"><ShieldCheck size={15}/> Admin access is restricted to your allowlisted email.</div>
          {!supabase && <div className="alert warning">Supabase environment variables are missing. Configure them in Vercel.</div>}
        </form>
        <div className="login-bottom">ProspectPilot <span>·</span> AI Lead Finder</div>
      </div>
    </div>
  );

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand"><div className="brand-mark"><Target size={19}/></div><span>Prospect<span className="brand-light">Pilot</span></span></div>
        <div className="workspace-switch"><div className="workspace-icon">W</div><div><b>My Workspace</b><small>Personal account</small></div><ChevronDown size={15}/></div>
        <div className="nav-label">WORKSPACE</div>
        <nav>
          <button className={`nav-item ${activeNav === "Overview" ? "active" : ""}`} onClick={() => setActiveNav("Overview")}><LayoutDashboard size={17}/> Overview</button>
          <button className={`nav-item ${activeNav === "All leads" ? "active" : ""}`} onClick={() => setActiveNav("All leads")}><Users size={17}/> All leads <span className="nav-count">{leads.length}</span></button>
          <button className={`nav-item ${activeNav === "Find companies" ? "active" : ""}`} onClick={() => setActiveNav("Find companies")}><Search size={17}/> Find companies</button>
        </nav>
        <div className="nav-label second">TOOLS</div>
        <div className="tool-card"><div className="tool-icon"><Zap size={17}/></div><div><b>Lead discovery</b><small>Public website research</small></div><span className="live-dot"/></div>
        <div className="sidebar-bottom">
          <div className="privacy-card"><ShieldCheck size={17}/><div><b>Privacy-first research</b><small>Public contacts · Human-reviewed outreach.</small></div></div>
          <div className="profile-row"><div className="profile-avatar">{initials(session.user.email)}</div><div className="profile-info"><b>{session.user.email.split("@")[0]}</b><small>Administrator</small></div><button className="icon-button" title="Sign out" onClick={signOut}><LogOut size={16}/></button></div>
        </div>
      </aside>

      <main className="main-area">
        <header className="topbar">
          <div className="breadcrumbs">Workspace <span>/</span> <b>{activeNav}</b></div>
          <div className="top-actions"><div className="status-pill"><span/> All systems operational</div><button className="help-button" title="Help"><CircleHelp size={17}/></button><div className="top-avatar">{initials(session.user.email)}</div></div>
        </header>

        <div className="page-content">
          <div className="page-heading">
            <div><div className="eyebrow">YOUR PROSPECTING COMMAND CENTER</div><h1>{activeNav === "Find companies" ? "Find your next clients" : activeNav === "All leads" ? "Your lead pipeline" : "Good to see you."}</h1><p>Discover software agencies and organize relevant business contacts.</p></div>
            <button className="secondary-button" onClick={exportCsv} disabled={!filteredLeads.length}><ArrowDownToLine size={16}/> Export CSV</button>
          </div>

          {notice && <div className="notice"><span>{notice}</span><button onClick={() => setNotice("")}><X size={15}/></button></div>}

          <section className="stats-grid">
            <div className="stat-card"><div className="stat-top"><span>Total prospects</span><div className="stat-icon purple"><Building2 size={17}/></div></div><strong>{leads.length}</strong><small><TrendingUp size={13}/> Saved companies</small></div>
            <div className="stat-card"><div className="stat-top"><span>Public inboxes found</span><div className="stat-icon blue"><Mail size={17}/></div></div><strong>{emailCount}</strong><small>Generic business addresses</small></div>
            <div className="stat-card"><div className="stat-top"><span>Strong matches</span><div className="stat-icon green"><BadgeCheck size={17}/></div></div><strong>{strongCount}</strong><small>Keyword relevance score 14+</small></div>
            <div className="stat-card"><div className="stat-top"><span>Markets covered</span><div className="stat-icon orange"><Globe2 size={17}/></div></div><strong>{countriesCount}</strong><small>Selected countries</small></div>
          </section>

          <section className="content-grid">
            <div className="panel discovery-panel">
              <div className="panel-heading"><div><div className="eyebrow">01 / DISCOVERY</div><h2>Build a prospect list</h2><p>Search the public web, choose official agency websites, then scan in small batches.</p></div><div className="panel-icon"><Sparkles size={19}/></div></div>
              <div className="form-grid">
                <label>Target market<select value={country} onChange={(e) => setCountry(e.target.value)}>{countries.map((c) => <option key={c}>{c}</option>)}</select></label>
                <label>Agency type<select value={niche} onChange={(e) => setNiche(e.target.value)}>{niches.map((n) => <option key={n}>{n}</option>)}</select></label>
              </div>
              <form onSubmit={runScan}>
                <div className="discovery-note" style={{marginTop:16,marginBottom:14,borderTop:0,paddingTop:0}}><CircleHelp size={15}/><span>Choose a country and business type. The system will discover company websites and scan them for publicly listed business inboxes.</span></div>
                <div className="scan-footer"><span><ShieldCheck size={14}/> Automatic discovery · Public business emails only</span><button className="primary" disabled={searching}>{searching ? <><Loader2 className="spin" size={16}/> Finding leads…</> : <><Search size={16}/> Find Leads</>}</button></div>
              </form>
              {searching && <div className="progress-wrap"><div className="progress-copy"><b>{progress.message}</b><span>{progress.done}/{progress.total || "…"}</span></div><div className="progress-track"><div style={{width: `${progress.total ? (progress.done / progress.total) * 100 : 8}%`}}/></div></div>}
              <div className="discovery-note"><CircleHelp size={15}/><span>Search engine access and website publishing determine how many results are available. Some businesses may not publish an email address.</span></div>
            </div>

            <div className="panel guide-panel">
              <div className="eyebrow">02 / YOUR WORKFLOW</div><h2>From search to conversation</h2>
              <div className="workflow-steps">
                <div className="workflow-step"><div className="step-num">1</div><div><b>Discover agencies</b><p>Use targeted search links to find relevant companies.</p></div></div>
                <div className="workflow-step"><div className="step-num">2</div><div><b>Scan official websites</b><p>Find generic business inboxes and source pages.</p></div></div>
                <div className="workflow-step"><div className="step-num">3</div><div><b>Review and qualify</b><p>Check each company before adding it to outreach.</p></div></div>
                <div className="workflow-step"><div className="step-num">4</div><div><b>Export your pipeline</b><p>Download CSV and prepare tailored messages.</p></div></div>
              </div>
              <div className="target-card"><div className="target-icon"><Target size={18}/></div><div><b>Weekly focus</b><p>Start with 20–30 well-matched agencies, not thousands of random contacts.</p></div></div>
            </div>
          </section>

          <section className="panel leads-panel">
            <div className="leads-header"><div><div className="eyebrow">03 / PIPELINE</div><h2>Recent prospects <span className="count-badge">{leads.length}</span></h2></div><button className="text-button" onClick={loadLeads}><Activity size={15}/> Refresh</button></div>
            <div className="table-toolbar"><div className="table-search"><Search size={16}/><input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search companies, websites or emails…"/></div><div className="filter-control"><SlidersHorizontal size={15}/><select value={filter} onChange={(e) => setFilter(e.target.value)}><option value="all">All statuses</option><option value="new">New</option><option value="reviewed">Reviewed</option><option value="contacted">Contacted</option><option value="replied">Replied</option><option value="interested">Interested</option><option value="not_a_fit">Not a fit</option></select></div></div>
            <div className="table-wrap">
              <table><thead><tr><th>COMPANY</th><th>PUBLIC BUSINESS EMAIL</th><th>FIT SCORE</th><th>STATUS</th><th>ADDED</th><th></th></tr></thead>
                <tbody>
                  {loadingLeads ? <tr><td colSpan="6" className="empty"><Loader2 className="spin"/> Loading leads…</td></tr> :
                    filteredLeads.length ? filteredLeads.map((lead) => <tr key={lead.id}>
                      <td><div className="company-cell"><div className="company-logo">{initials(lead.company)}</div><div><b>{lead.company}</b><a href={lead.website} target="_blank" rel="noreferrer">{lead.website.replace(/^https?:\/\//, "").replace(/\/$/, "")} <ArrowUpRight size={11}/></a><button className="text-button draft-trigger" onClick={() => createEmailDraft(lead)}><FileText size={13}/> Write outreach email</button></div></div></td>
                      <td>{(lead.business_emails || []).length ? <div className="email-list">{lead.business_emails.slice(0, 2).map((mail) => <button key={mail} onClick={() => copy(mail)} title="Copy email"><Mail size={12}/>{mail}<Copy size={11}/></button>)}{lead.business_emails.length > 2 && <small>+{lead.business_emails.length - 2} more</small>}</div> : <span className="muted">No public inbox found</span>}</td>
                      <td><div className="score"><div className="score-track"><i style={{width: `${(lead.lead_score || 0) * 4}%`}}/></div><b>{lead.lead_score || 0}</b><small>{scoreLabel(lead.lead_score)}</small></div></td>
                      <td><select className={`status-select ${lead.status}`} value={lead.status} onChange={(e) => updateStatus(lead.id, e.target.value)}><option value="new">New</option><option value="reviewed">Reviewed</option><option value="contacted">Contacted</option><option value="replied">Replied</option><option value="interested">Interested</option><option value="not_a_fit">Not a fit</option></select></td>
                      <td className="date-cell">{formatDate(lead.created_at)}</td>
                      <td><button className="icon-button danger-hover" onClick={() => deleteLead(lead.id)} title="Delete lead"><Trash2 size={15}/></button></td>
                    </tr>) :
                    <tr><td colSpan="6"><div className="empty-state"><div className="empty-icon"><Users size={22}/></div><b>No prospects yet</b><p>Find companies, collect public contact emails, then create a tailored outreach message.</p></div></td></tr>}
                </tbody>
              </table>
            </div>
            <div className="table-bottom"><span>Showing {filteredLeads.length} of {leads.length} prospects</span><span><ShieldCheck size={13}/> Your leads are private to your account</span></div>
          </section>

          {emailLead && <div className="modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) setEmailLead(null); }}>
            <section className="email-modal" role="dialog" aria-modal="true" aria-label="Create outreach email">
              <div className="modal-heading"><div><div className="eyebrow">PERSONALIZED OUTREACH</div><h2>Write to {emailLead.company}</h2><p className="muted">Review and edit this message before sending.</p></div><button className="icon-button" onClick={() => setEmailLead(null)}><X size={18}/></button></div>
              <div className="modal-fields">
                <label>Your name<input value={senderName} onChange={(event) => setSenderName(event.target.value)} placeholder="Your name"/></label>
                <label>What you offer<input value={service} onChange={(event) => setService(event.target.value)} placeholder="Your development services"/></label>
                <label>To<input type="email" value={recipientEmail} onChange={(event) => setRecipientEmail(event.target.value)} placeholder="Enter recipient email"/></label>
                <label>Subject<input value={draftSubject} onChange={(event) => setDraftSubject(event.target.value)} placeholder={draftBusy ? "Generating subject…" : "Email subject"}/></label>
                <label>Message<textarea rows={10} value={draftBody} onChange={(event) => setDraftBody(event.target.value)} placeholder={draftBusy ? "Preparing your personalized email…" : "Your email draft will appear here."}/></label>
              </div>
              <div className="modal-actions">
                <button className="secondary-button" onClick={() => copy(`Subject: ${draftSubject}\n\n${draftBody}`)} disabled={!draftBody}><Copy size={15}/> Copy email</button>
                <button className="primary" onClick={openMailClient} disabled={!draftBody || !draftSubject || !recipientEmail.includes("@")}><Send size={15}/> Open in email app</button>
              </div>
              <p className="modal-footnote">This opens your email app with the recipient, subject and message filled in. You review and send it yourself.</p>
            </section>
          </div>}
          <footer className="footer"><span>© 2026 ProspectPilot</span><span>Public business contacts only <i/> Research responsibly</span></footer>
        </div>
      </main>
    </div>
  );
}

export default App;

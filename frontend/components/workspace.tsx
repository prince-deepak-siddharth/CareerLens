"use client";

import { QueryClient, QueryClientProvider, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowDownRight, ArrowLeft, ArrowRight, ArrowUpRight, BarChart3, Bookmark, BriefcaseBusiness,
  ChevronDown, ChevronRight, CircleHelp, Compass, FileText, Layers3, LayoutDashboard, LogOut,
  MapPin, Menu, Search as SearchIcon, Send, Sparkles, Upload, UserRound, X,
} from "lucide-react";
import { useEffect, useRef, useState, useSyncExternalStore, type FormEvent, type ReactNode } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Button } from "@/components/ui/button";
import { request, type Account, type Catalog, type Conversation, type Draft, type EvalRun, type Filters, type Job,
  type Match, type Profile, type ProfileView, type ResumeExtractor, type Results, type Turn } from "@/lib/api";

const nav = [
  { key: "Dashboard", icon: LayoutDashboard }, { key: "Discover jobs", icon: Compass },
  { key: "Career agent", icon: Sparkles }, { key: "Resume & profile", icon: FileText },
  { key: "Saved jobs", icon: Bookmark },
] as const;
type View = typeof nav[number]["key"] | "Evaluations" | "Observability";
type Detail = { job: Job; match?: Match; saved?: boolean };
const blankProfile: Profile = { education: [], skills: [], projects: [], work_experience: [],
  tools: [], certifications: [], domains: [], experience_level: null, years_experience: null };
const profileFields = ["skills", "education", "projects", "work_experience", "tools", "certifications", "domains"] as const;
const prompts = ["Find live AI internships", "Search InternDoor for data science internships", "Show my saved jobs"];
const message = (error: unknown) => error instanceof Error ? error.message : "Something went wrong";
const label = (value: string) => value.replaceAll("_", " ").replace(/^./, char => char.toUpperCase());
const metricLabel = (value: string) => label(value).replace(/\bndcg\b/i, "NDCG").replace(/\bmrr\b/i, "MRR");
const formatDate = (value: string) => new Date(value).toLocaleDateString(undefined, {month: "short", day: "numeric", year: "numeric"});

function Brand({ onClick }: { onClick?: () => void }) {
  return <button onClick={onClick} className="brand" type="button">
    <span className="brand-glyph"><Compass size={21} strokeWidth={2.1} /></span>
    <span>Career<span className="brand-accent">Lens</span></span>
  </button>;
}

function Landing({ onAuth }: { onAuth: (mode: "login" | "register") => void }) {
  return <main className="landing">
    <header className="landing-header">
      <Brand /><div className="button-row">
        <Button variant="ghost" onClick={() => onAuth("login")}>Sign in</Button>
        <Button onClick={() => onAuth("register")}>Get started <ArrowUpRight size={15} /></Button>
      </div>
    </header>
    <section className="hero">
      <div className="hero-copy">
        <span className="kicker"><Sparkles size={14} /> AI career workspace</span>
        <h1>Find work that<br/>fits <em>you.</em></h1>
        <p>Turn your skills, projects, and preferences into a focused job search. Explore opportunities and see the evidence behind each match.</p>
        <div className="button-row">
          <Button className="hero-cta" onClick={() => onAuth("register")}>Create your workspace <ArrowRight size={18} /></Button>
          <a className="text-link" href="#workflow">How it works <ArrowDownRight size={16} /></a>
        </div>
      </div>
      <div className="hero-visual" aria-hidden="true">
        <div className="orb orb-one"/><div className="orb orb-two"/>
        <div className="hero-board">
          <div className="board-title"><span className="agent-glyph"><Sparkles size={21}/></span><div><strong>Career agent</strong><small>Explore opportunities</small></div></div>
          <div className="hero-prompt">Find remote engineering internships that match my Python projects.</div>
          <div className="hero-board-section"><SearchIcon size={16}/><span>Search preferences</span></div>
          <div className="button-row"><span className="pill">Remote</span><span className="pill">Internship</span><span className="pill">Python</span></div>
          <div className="hero-board-foot"><Layers3 size={17}/> Check requirements and source evidence <ChevronRight size={15}/></div>
        </div>
      </div>
    </section>
    <section className="workflow" id="workflow">
      <h2>From resume to shortlist</h2>
      <div className="workflow-grid">
        <div><FileText/><h3>Build your profile</h3><p>Upload a PDF and review the extracted skills, education, and experience.</p></div>
        <div><Compass/><h3>Discover roles</h3><p>Search by skill or describe what you’re looking for. Refine by location, experience, and more.</p></div>
        <div><Sparkles/><h3>Ask why it fits</h3><p>Compare requirements with your confirmed profile and save the jobs you want to revisit.</p></div>
      </div>
    </section>
    <footer className="landing-footer"><Brand /><span>Career intelligence for early-career engineers</span></footer>
  </main>;
}

function AuthForm({ mode, setMode, onToken }: {
  mode: "login" | "register"; setMode: (mode: "login" | "register" | null) => void; onToken: (token: string) => void;
}) {
  const mutation = useMutation({ mutationFn: (body: object) => request<{ access_token: string }>("", "/auth/" + mode,
    { method: "POST", body: JSON.stringify(body) }), onSuccess: data => onToken(data.access_token) });
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    mutation.mutate({ email: data.get("email"), password: data.get("password") });
  }
  return <main className="auth-page"><header><Brand onClick={() => setMode(null)}/></header>
    <div className="auth-container">
      <Button className="back-button" variant="ghost" onClick={() => setMode(null)}><ArrowLeft size={16}/> Home</Button>
      <section className="panel auth-panel">
        <span className="agent-glyph"><UserRound size={22}/></span>
        <h1>{mode === "login" ? "Welcome back" : "Create your workspace"}</h1>
        <p>{mode === "login" ? "Sign in to continue your job search." : "Start with an email address and password."}</p>
        <form onSubmit={submit} className="form-stack">
          <label>Email address<input name="email" type="email" autoComplete="email" placeholder="you@example.com" required/></label>
          <label>Password<input name="password" type="password" autoComplete={mode === "login" ? "current-password" : "new-password"}
            minLength={12} maxLength={128} placeholder="At least 12 characters" required/></label>
          {mutation.isError && <ErrorText>{message(mutation.error)}</ErrorText>}
          <Button disabled={mutation.isPending}>{mutation.isPending ? "Please wait…" : mode === "login" ? "Sign in" : "Create account"}
            <ArrowRight size={16}/></Button>
        </form>
        <p className="auth-switch">{mode === "login" ? "New to CareerLens?" : "Already have an account?"}{" "}
          <button className="text-link" onClick={() => { mutation.reset(); setMode(mode === "login" ? "register" : "login"); }}>
            {mode === "login" ? "Create an account" : "Sign in"}</button></p>
      </section>
    </div>
  </main>;
}

function ErrorText({ children }: { children: ReactNode }) {
  return <p className="error" role="alert">{children}</p>;
}

function Empty({ icon = <SearchIcon size={22}/>, title, description }: {
  icon?: ReactNode; title: string; description: string;
}) {
  return <div className="empty"><span className="agent-glyph">{icon}</span><h3>{title}</h3><p>{description}</p></div>;
}

function Heading({ title, copy, action }: { title: string; copy: string; action?: ReactNode }) {
  return <header className="page-heading"><div><h1>{title}</h1><p>{copy}</p></div>{action}</header>;
}

function JobCard({ job, match, saved, onOpen }: { job: Job; match?: Match; saved?: boolean; onOpen: (detail: Detail) => void }) {
  return <button className="job-card panel" onClick={() => onOpen({ job, match, saved })}>
    <div className="job-top">
      <span className="job-icon">{job.company.split(" ").slice(0, 2).map(part => part[0]).join("")}</span>
      {match?.score != null && <span className="match-score">{Math.round(match.score)} match</span>}
      {saved && <Bookmark size={18} className="saved-icon" fill="currentColor"/>}
    </div>
    <div><h3>{job.title}</h3><p className="job-company">{job.company}</p></div>
    <div className="job-meta"><MapPin size={14}/>{job.location || "Location unavailable"}
      {job.remote_policy && <><span>·</span>{label(job.remote_policy)}</>}</div>
    <div className="job-bottom"><div className="pills">
      {job.synthetic ? <span className="pill demo-pill">Demo listing</span> :
        <span className="pill">{job.source_name.split(" · ")[0]}</span>}
      {job.required_skills.slice(0, 2).map(skill => <span key={skill} className="pill">{skill}</span>)}
    </div><ArrowUpRight size={17}/></div>
  </button>;
}

function JobDetail({ detail, token, onClose }: { detail: Detail; token: string; onClose: () => void }) {
  const { job, match } = detail;
  const cache = useQueryClient();
  const modal = useRef<HTMLDialogElement>(null);
  const savedJobs = useQuery({ queryKey: ["saved", token], queryFn: () => request<Job[]>(token, "/jobs/saved") });
  const [savedOverride, setSaved] = useState<boolean>();
  const isSaved = savedOverride ?? (savedJobs.data ? savedJobs.data.some(item => item.id === job.id) : Boolean(detail.saved));
  useEffect(() => { modal.current?.showModal(); }, []);
  const save = useMutation({
    mutationFn: () => request(token, "/jobs/" + job.id + "/save", { method: isSaved ? "DELETE" : "POST" }),
    onSuccess: () => { setSaved(!isSaved); void cache.invalidateQueries({ queryKey: ["saved", token] }); },
  });
  const feedback = useMutation({ mutationFn: (kind: string) => request(token, "/recommendations/feedback", {
    method: "POST", body: JSON.stringify({ job_id: job.id, kind }),
  }), onSuccess: () => {
    void cache.invalidateQueries({ queryKey: ["recommendations", token] });
    void cache.invalidateQueries({ queryKey: ["search", token] });
  }});
  return <dialog ref={modal} onCancel={onClose} className="job-dialog">
    <div className="dialog-head"><span className="job-icon"><BriefcaseBusiness size={22}/></span>
      <Button variant="ghost" onClick={onClose} aria-label="Close job details"><X size={20}/></Button>
    </div>
    <h2>{job.title}</h2><p className="job-company">
      {[job.company, job.location, job.remote_policy && label(job.remote_policy)].filter(Boolean).join(" · ")}</p>
    <div className="pills">
      {job.employment_type && <span className="pill">{label(job.employment_type)}</span>}
      {job.min_years != null && <span className="pill">{job.min_years} years required</span>}
      {!job.active && <span className="pill">Closed</span>}
      {job.synthetic && <span className="pill demo-pill">Synthetic demo listing</span>}
      {job.expires_at && new Date(job.expires_at) < new Date() && <span className="pill">Expired</span>}
    </div>
    <section><h3>About the role</h3><p className="evidence">{job.description}</p></section>
    <section><h3>Required skills</h3><div className="pills">
      {job.required_skills.map(skill => <span className="pill" key={skill}>{skill}</span>)}
      {job.required_skills.length === 0 && <p>See the original posting for requirements.</p>}
    </div></section>
    {!!job.preferred_skills.length && <section><h3>Preferred skills</h3><div className="pills">
      {job.preferred_skills.map(skill => <span className="pill" key={skill}>{skill}</span>)}
    </div></section>}
    <section><h3>Posting information</h3>
      <p>Source {job.source_name}{job.posted_at && " · Posted " + formatDate(job.posted_at)}</p>
      {job.source_provenance && /^https?:\/\//.test(job.source_provenance) &&
        <a href={job.source_provenance} target="_blank" rel="noreferrer" className="text-link">Source feed <ArrowUpRight size={14}/></a>}
      {job.synthetic && <p>Sample posting generated by CareerLens.</p>}
      {(job.salary_min != null || job.salary_max != null) && <p>Compensation {job.currency} {
        job.salary_min != null && job.salary_max != null ? job.salary_min.toLocaleString() + "–" + job.salary_max.toLocaleString() :
        (job.salary_min ?? job.salary_max)?.toLocaleString()
      }</p>}
    </section>
    {match && <section className="match-explanation">
      <h3><Sparkles size={17}/> Why this matches{match.score != null ? " · " + match.score : ""}</h3>
      <p>{match.explanation}</p>
      {!!match.missing_skills.length && <p>Skills to build · {match.missing_skills.join(", ")}</p>}
      {match.eligibility_warnings.map(warning => <p key={warning}>{warning}</p>)}
      <blockquote>{match.evidence}</blockquote>
    </section>}
    <div className="button-row dialog-actions">
      <Button disabled={save.isPending} onClick={() => save.mutate()}><Bookmark size={16}/>{isSaved ? "Remove saved job" : "Save job"}</Button>
      {job.source_url && <Button variant="outline" asChild><a href={job.source_url} target="_blank" rel="noreferrer">Original posting <ArrowUpRight size={15}/></a></Button>}
    </div>
    {save.isError && <ErrorText>{message(save.error)}</ErrorText>}
    {match && <div className="feedback-row">
      {["helpful", "unhelpful", "incorrect", "unsupported", "dismiss"].map(kind =>
        <button key={kind} disabled={feedback.isPending} onClick={() => feedback.mutate(kind)}>
          {{ helpful: "Helpful", unhelpful: "Unhelpful", incorrect: "Report job info", unsupported: "Flag explanation", dismiss: "Dismiss" }[kind]}
        </button>)}
      {feedback.isSuccess && <span role="status">Feedback recorded.</span>}
      {feedback.isError && <ErrorText>{message(feedback.error)}</ErrorText>}
    </div>}
  </dialog>;
}

function Dashboard({ token, account, go, onOpen }: {
  token: string; account: Account; go: (view: View) => void; onOpen: (detail: Detail) => void;
}) {
  const profile = useQuery({ queryKey: ["profile", token], queryFn: () => request<ProfileView>(token, "/profile") });
  const saved = useQuery({ queryKey: ["saved", token], queryFn: () => request<Job[]>(token, "/jobs/saved") });
  const recommendations = useQuery({ queryKey: ["recommendations", token],
    queryFn: () => request<Results>(token, "/recommendations") });
  const name = account.email.split("@")[0].split(/[._-]/)[0];
  return <>
    <Heading title={"Welcome, " + label(name)} copy="Find opportunities that fit your skills and goals."/>
    <section className="dashboard-banner">
      <div><span className="banner-icon"><Sparkles size={20}/></span><h2>Your next role starts here.</h2>
        <p>Describe the work you want to do. Your career agent can refine the search with you.</p>
        <Button onClick={() => go("Career agent")}>Open career agent <ArrowRight size={16}/></Button></div>
      <div className="banner-art" aria-hidden="true"><Compass strokeWidth={.7} size={178}/></div>
    </section>
    <div className="quick-grid">
      <button className="panel quick-card" onClick={() => go("Resume & profile")}>
        <span className="quick-icon"><FileText size={19}/></span>
        <span><strong>Candidate profile</strong><small>{profile.data ? profile.data.profile.skills.length + " confirmed skills" : "Review your profile"}</small></span>
        <ChevronRight size={18}/>
      </button>
      <button className="panel quick-card" onClick={() => go("Saved jobs")}>
        <span className="quick-icon purple"><Bookmark size={19}/></span>
        <span><strong>Saved jobs</strong><small>{saved.data ? saved.data.length + " saved opportunities" : "View saved opportunities"}</small></span>
        <ChevronRight size={18}/>
      </button>
    </div>
    <div className="section-heading"><h2>Recommended opportunities</h2><button className="text-link" onClick={() => go("Discover jobs")}>Browse all jobs <ArrowRight size={15}/></button></div>
    {recommendations.isPending && <p className="loading">Loading recommendations…</p>}
    {recommendations.isError && <ErrorText>{message(recommendations.error)}</ErrorText>}
    {recommendations.data?.items.length === 0 && <Empty title="No recommendations yet" description="Try a broader search or add your skills to your profile."/>}
    <div className="job-grid">{recommendations.data?.items.slice(0, 6).map(item =>
      <JobCard key={item.job.id} job={item.job} match={item} saved={saved.data?.some(job => job.id === item.job.id)} onOpen={onOpen}/>)}</div>
  </>;
}

function FilterSelect({ name, title, options, value }: { name: string; title: string; options: string[]; value?: string | null }) {
  return <label>{title}<select name={name} defaultValue={value ?? ""}><option value="">Any</option>
    {options.map(option => <option key={option} value={option}>{label(option)}</option>)}</select></label>;
}

function Discover({ token, onOpen }: { token: string; onOpen: (detail: Detail) => void }) {
  const [view, setView] = useState<"catalog" | "search">("catalog");
  const [source, setSource] = useState("");
  const [page, setPage] = useState(1);
  const [query, setQuery] = useState<{ query: string; filters: Filters; mode: string }>();
  const catalog = useQuery({ queryKey: ["catalog", token, source, page], enabled: view === "catalog",
    queryFn: () => request<Catalog>(token, "/jobs/catalog?" + new URLSearchParams({
      page: String(page), ...(source ? { source } : {}),
    })) });
  const [advanced, setAdvanced] = useState(false);
  const results = useQuery({ queryKey: ["search", token, query], enabled: view === "search" && Boolean(query), queryFn: () => request<Results>(token, "/jobs/search", {
    method: "POST", body: JSON.stringify(query),
  }) });
  const saved = useQuery({ queryKey: ["saved", token], queryFn: () => request<Job[]>(token, "/jobs/saved") });
  function search(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const fields = new FormData(event.currentTarget);
    const str = (key: string) => String(fields.get(key) ?? "").trim();
    const num = (key: string) => str(key) ? Number(str(key)) : null;
    setQuery({ query: str("query"), mode: str("mode") || "semantic", filters: {
      source: (str("source") || null) as Filters["source"],
      employment_type: str("employment_type") || null, remote_policy: str("remote_policy") || null,
      location: str("location") || null, max_required_years: num("experience"),
      skills: str("skills").split(",").map(value => value.trim()).filter(Boolean),
      company_category: str("company_category") || null, posted_within_days: num("freshness"),
      min_salary: num("salary"), currency: str("currency").toUpperCase() || null,
    } });
  }
  return <>
    <Heading title="Discover jobs" copy="Browse the catalog or search for a role by meaning, keyword, and work preferences."/>
    <div className="job-tabs button-row" aria-label="Job view">
      <Button variant={view === "catalog" ? "primary" : "outline"} aria-pressed={view === "catalog"}
        onClick={() => setView("catalog")}>All jobs</Button>
      <Button variant={view === "search" ? "primary" : "outline"} aria-pressed={view === "search"}
        onClick={() => setView("search")}>Search jobs</Button>
    </div>
    {view === "catalog" && <>
      <section className="panel content-panel catalog-intro" aria-label="Job sources">
        <div><h2>Job catalog</h2>
          <p>{catalog.data ? catalog.data.total.toLocaleString() + " active postings" : "Active postings"}</p></div>
        <label>Source<select value={source} onChange={event => { setSource(event.target.value); setPage(1); }}>
          <option value="">All sources</option><option value="live">Employer postings</option><option value="demo">Demo listings</option>
        </select></label>
        <div className="pills catalog-sources">{catalog.data?.sources.map(item =>
          /^https?:\/\//.test(item.provenance) ? <a key={item.name} className="pill source-link" href={item.provenance}
            target="_blank" rel="noreferrer">{item.name} · {item.total.toLocaleString()} <ArrowUpRight size={12}/></a> :
            <span key={item.name} className="pill demo-pill">{item.name} · {item.total.toLocaleString()}</span>)}</div>
      </section>
      {catalog.isPending && <p className="loading">Loading job catalog…</p>}
      {catalog.isError && <ErrorText>{message(catalog.error)}</ErrorText>}
      {catalog.data?.items.length === 0 && <Empty title="No postings here" description="Choose another source to see more jobs."/>}
      <div className="job-grid">{catalog.data?.items.map(job => <JobCard key={job.id} job={job} onOpen={onOpen}
        saved={saved.data?.some(savedJob => savedJob.id === job.id)}/>)}</div>
      {!!catalog.data?.total && <nav className="button-row catalog-pagination" aria-label="Job pages">
        <Button variant="outline" disabled={page < 2} onClick={() => setPage(page - 1)}><ArrowLeft size={16}/> Previous</Button>
        <span>Page {page} of {Math.ceil(catalog.data.total / catalog.data.page_size)}</span>
        <Button variant="outline" disabled={page * catalog.data.page_size >= catalog.data.total}
          onClick={() => setPage(page + 1)}>Next <ArrowRight size={16}/></Button>
      </nav>}
    </>}
    <section hidden={view !== "search"}>
    <form className="panel search-form" onSubmit={search}>
      <div className="search-line"><div className="search-input"><SearchIcon size={20}/><input aria-label="Search jobs" name="query" maxLength={600} placeholder="Try “machine learning internships”"/></div>
        <Button>Search jobs <ArrowRight size={16}/></Button></div>
      <div className="filter-row">
        <label>Search mode<select name="mode" defaultValue="semantic">
          <option value="semantic">Semantic · Search by meaning</option><option value="keyword">Keyword · Exact terms</option>
          <option value="hybrid">Hybrid · Meaning and keywords</option><option value="personalized">Personalized</option>
          <option value="reranked">Reranked hybrid</option>
        </select></label>
        <label>Source<select name="source"><option value="">All sources</option>
          <option value="live">Employer postings</option><option value="demo">Demo listings</option>
        </select></label>
        <FilterSelect name="employment_type" title="Employment" options={["internship", "full-time", "part-time", "contract"]}/>
        <FilterSelect name="remote_policy" title="Work setting" options={["remote", "hybrid", "onsite"]}/>
        <label>Location<input name="location" placeholder="Any location" maxLength={120}/></label>
        <label>Maximum experience<input name="experience" placeholder="Any years" type="number" min={0} max={70}/></label>
      </div>
      <button className="text-link" type="button" aria-expanded={advanced} onClick={() => setAdvanced(!advanced)}>More filters <ChevronDown size={15}/></button>
      <div className="filter-row" hidden={!advanced}>
        <label>Required skills<input name="skills" placeholder="Python, SQL"/></label>
        <FilterSelect name="company_category" title="Company category" options={["startup", "enterprise", "nonprofit"]}/>
        <label>Posted within days<input name="freshness" type="number" min={1} max={365}/></label>
        <label>Minimum salary<input name="salary" type="number" min={1} max={10000000}/></label>
        <label>Salary currency<input name="currency" minLength={3} maxLength={3} placeholder="USD"/></label>
      </div>
    </form>
    {query && <div className="section-heading"><h2>Search results</h2>{results.data && <span>
      {results.data.items.length} shown{results.data.embedding_model && " · " + results.data.embedding_model.split("/").at(-1)}
    </span>}</div>}
    {query?.mode === "reranked" && results.data && !results.data.reranked && <p className="loading">Cross-encoder unavailable. Showing hybrid results.</p>}
    {results.isFetching && <p className="loading">Searching opportunities…</p>}
    {results.isError && <ErrorText>{message(results.error)}</ErrorText>}
    {results.data?.items.length === 0 && <Empty title="No matching jobs" description="Broaden your query or adjust your filters."/>}
    <div className="job-grid">{results.data?.items.map(item =>
      <JobCard key={item.job.id} job={item.job} match={item} onOpen={onOpen}
        saved={saved.data?.some(job => job.id === item.job.id)}/>)}</div>
    </section>
  </>;
}

function SavedJobs({ token, onOpen }: { token: string; onOpen: (detail: Detail) => void }) {
  const query = useQuery({ queryKey: ["saved", token], queryFn: () => request<Job[]>(token, "/jobs/saved") });
  return <><Heading title="Saved jobs" copy="Keep track of opportunities you want to return to."/>
    {query.isPending && <p className="loading">Loading saved jobs…</p>}
    {query.isError && <ErrorText>{message(query.error)}</ErrorText>}
    {query.data?.length === 0 && <Empty icon={<Bookmark size={22}/>} title="No saved jobs" description="Open a posting and select Save job to add it here."/>}
    <div className="job-grid">{query.data?.map(job => <JobCard key={job.id} job={job} saved onOpen={onOpen}/>)}</div>
  </>;
}

function ResumeProfile({ token }: { token: string }) {
  const cache = useQueryClient();
  const profile = useQuery({ queryKey: ["profile", token], queryFn: () => request<ProfileView>(token, "/profile") });
  const resumes = useQuery({ queryKey: ["resumes", token], queryFn: () => request<Draft[]>(token, "/resumes") });
  const extractor = useQuery({ queryKey: ["resume-extractor", token],
    queryFn: () => request<ResumeExtractor>(token, "/resumes/extractor") });
  const [draft, setDraft] = useState<Draft | null>(null);
  const [version, setVersion] = useState(0);
  const upload = useMutation({ mutationFn: (form: FormData) => request<Draft>(token, "/resumes/upload", {
    method: "POST", body: form,
  }), onSuccess: data => {
    setDraft(data);
    void cache.invalidateQueries({ queryKey: ["resumes", token] });
  }});
  const update = useMutation({
    mutationFn: (body: { profile?: Profile; preferences?: Filters }) => request<ProfileView>(token,
      draft && body.profile ? "/resumes/" + draft.id + "/confirm" : "/profile",
      { method: draft && body.profile ? "POST" : "PATCH", body: JSON.stringify(body) }),
    onSuccess: (value, body) => {
      cache.setQueryData(["profile", token], value);
      if (body.profile) setDraft(null);
      setVersion(version + 1);
      void cache.invalidateQueries({ queryKey: ["resumes", token] });
      void cache.invalidateQueries({ queryKey: ["recommendations", token] });
    },
  });
  const remove = useMutation({ mutationFn: (identifier: string) => request(token, "/resumes/" + identifier, { method: "DELETE" }),
    onSuccess: (_result, id) => {
      if (draft?.id === id) setDraft(null);
      void cache.invalidateQueries({ queryKey: ["resumes", token] });
    },
  });
  function saveProfile(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const fields = new FormData(event.currentTarget);
    const result = { ...blankProfile };
    for (const field of profileFields) {
      result[field] = String(fields.get(field) ?? "").split(field === "skills" ? /[\n,]/ : /\n/).map(value => value.trim()).filter(Boolean);
    }
    result.experience_level = String(fields.get("experience_level") || "") || null;
    result.years_experience = fields.get("experience") === "" ? null : Number(fields.get("experience"));
    update.mutate({ profile: result });
  }
  function savePreferences(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const fields = new FormData(event.currentTarget);
    const text = (key: string) => String(fields.get(key) ?? "").trim() || null;
    update.mutate({ preferences: {
      ...profile.data?.preferences, remote_policy: text("remote_policy"), employment_type: text("employment_type"),
      location: text("location"), skills: (text("skills") ?? "").split(",").map(skill => skill.trim()).filter(Boolean),
    } });
  }
  const values = draft?.draft ?? profile.data?.profile ?? blankProfile;
  const preferences = profile.data?.preferences;
  return <>
    <Heading title="Resume & profile" copy="Review what CareerLens knows about your skills, experience, and preferences."/>
    <div className="profile-grid">
      <section className="panel content-panel">
        <h2>Resume upload</h2><p>Upload a PDF with selectable text, up to 5 MB and 10 pages.</p>
        {extractor.data?.model && extractor.data.configured && <p>Resume text is processed by {label(extractor.data.provider)}.
          You can review the extracted details before adding them to your profile.</p>}
        {extractor.data?.model && !extractor.data.configured && <ErrorText>Resume extraction is waiting for a provider key.</ErrorText>}
        <form className="upload" onSubmit={event => { event.preventDefault(); upload.mutate(new FormData(event.currentTarget)); }}>
          <span className="agent-glyph"><Upload size={21}/></span>
          <label>Choose a PDF<input name="file" type="file" accept="application/pdf,.pdf" required/></label>
          <Button disabled={upload.isPending || extractor.data?.configured === false}>
            {upload.isPending ? "Extracting…" : extractor.data?.model ? "Extract with AI" : "Extract resume"}</Button>
        </form>
        {upload.isError && <ErrorText>{message(upload.error)}</ErrorText>}
        {resumes.isError && <ErrorText>{message(resumes.error)}</ErrorText>}
        {remove.isError && <ErrorText>{message(remove.error)}</ErrorText>}
        {resumes.data?.map((item, index) => <div className="resume-row" key={item.id}>
          <span><FileText size={17}/> Resume {resumes.data.length - index} · {item.confirmed ? "Confirmed" : "Draft"}</span>
          <button className="text-link" onClick={() => setDraft(item)}>Review</button>
          <button className="text-link" onClick={() => remove.mutate(item.id)} disabled={remove.isPending}>Delete</button>
        </div>)}
        <h2 className="preferences-title">Saved preferences</h2>
        {profile.isError && <ErrorText>{message(profile.error)}</ErrorText>}
        {preferences && <form key={"prefs-" + version} className="form-stack" onSubmit={savePreferences}>
          <FilterSelect name="remote_policy" title="Preferred work setting" options={["remote", "hybrid", "onsite"]} value={preferences.remote_policy}/>
          <FilterSelect name="employment_type" title="Preferred employment" options={["internship", "full-time", "part-time", "contract"]}
            value={preferences.employment_type}/>
          <label>Preferred location<input name="location" defaultValue={preferences.location ?? ""} maxLength={120}/></label>
          <label>Preferred skills<input name="skills" defaultValue={preferences.skills?.join(", ")}/></label>
          <Button variant="outline" disabled={update.isPending}>Save preferences</Button>
        </form>}
      </section>
      <section className="panel content-panel">
        <div className="section-heading compact"><h2>{draft ? "Review extracted profile" : "Candidate profile"}</h2>
          {draft && <button className="text-link" onClick={() => setDraft(null)}>Close review</button>}</div>
        <p>{draft ? "Review the extracted details and select Add to profile to save them." : "Confirmed information helps rank jobs and explain matches."}</p>
        {draft?.extraction_model && <p>Extracted with {draft.extraction_model}</p>}
        <form key={(draft?.id ?? "profile") + version + (profile.isSuccess ? "-loaded" : "")} className="form-stack" onSubmit={saveProfile}>
          {profileFields.map(field => <label key={field}>{label(field)}
            <textarea rows={field === "skills" ? 2 : 1} name={field} defaultValue={values[field].join("\n")}
              placeholder="One item per line"/></label>)}
          <FilterSelect name="experience_level" title="Experience level" options={["student", "entry", "mid", "senior"]} value={values.experience_level}/>
          <label>Years of experience<input name="experience" type="number" min={0} max={70} defaultValue={values.years_experience ?? ""}/></label>
          {update.isError && <ErrorText>{message(update.error)}</ErrorText>}
          {update.isSuccess && <p className="success" role="status">Changes saved.</p>}
          <Button disabled={update.isPending || profile.isPending}>{draft ? "Add to profile" : "Save profile"} <ArrowRight size={16}/></Button>
        </form>
      </section>
    </div>
  </>;
}

function Agent({ token, onOpen }: { token: string; onOpen: (detail: Detail) => void }) {
  const cache = useQueryClient();
  const [thread, setThread] = useState("");
  const [input, setInput] = useState("");
  const [optimistic, setOptimistic] = useState<string>();
  const conversation = useQuery({ queryKey: ["conversations", token],
    queryFn: () => request<Conversation[]>(token, "/agent/conversations") });
  const history = useQuery({ queryKey: ["history", token, thread], enabled: Boolean(thread),
    queryFn: () => request<{ messages: Turn[] }>(token, "/agent/conversations/" + thread) });
  const send = useMutation({
    mutationFn: (text: string) => request<Turn & { conversation_id: string }>(token, "/agent/chat", {
      method: "POST", body: JSON.stringify({ message: text, conversation_id: thread || null }),
    }),
    onMutate: text => setOptimistic(text),
    onSuccess: (reply, text) => {
      const key = ["history", token, reply.conversation_id];
      cache.setQueryData<{ messages: Turn[] }>(key, old => ({ messages: [
        ...(old?.messages ?? []), { role: "user", message: text }, { ...reply, role: "assistant" },
      ] }));
      setThread(reply.conversation_id);
      setInput("");
      void cache.invalidateQueries({ queryKey: ["conversations", token] });
      void cache.invalidateQueries({ queryKey: ["profile", token] });
      void cache.invalidateQueries({ queryKey: ["saved", token] });
    },
    onSettled: () => setOptimistic(undefined),
  });
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (input.trim()) send.mutate(input.trim());
  }
  return <>
    <Heading title="Career agent" copy="Search jobs, refine your shortlist, and ask about the evidence behind a match."
      action={<Button variant="outline" disabled={send.isPending} onClick={() => { setThread(""); send.reset(); setInput(""); }}>
        <Sparkles size={16}/> New conversation</Button>}/>
    <div className="chat-grid">
      <aside className="panel conversations"><h2>Conversations</h2>
        {conversation.isError && <ErrorText>{message(conversation.error)}</ErrorText>}
        {conversation.data?.length === 0 && <p>Your conversations will appear here.</p>}
        {conversation.data?.map(item => <button key={item.id} type="button" disabled={send.isPending}
          className={thread === item.id ? "selected" : ""} onClick={() => { setThread(item.id); send.reset(); }}>
          <span>{item.title}</span><ChevronRight size={15}/></button>)}
      </aside>
      <section className="panel chat-panel" aria-label="Conversation">
        <header className="chat-header"><span className="agent-glyph"><Sparkles size={19}/></span>
          <div><strong>Career agent</strong><small>Searches public job sources and uses your confirmed profile</small></div>
        </header>
        <div className="chat-content" aria-live="polite">
          {!thread && !optimistic && <div className="chat-intro">
            <span className="agent-glyph"><Sparkles size={25}/></span><h2>What are you looking for?</h2>
            <div className="prompt-grid">{prompts.map(prompt => <button key={prompt} type="button" onClick={() => send.mutate(prompt)}>
              {prompt}<ArrowUpRight size={15}/></button>)}</div>
          </div>}
          {thread && history.isPending && <p className="loading">Loading conversation…</p>}
          {history.isError && <ErrorText>{message(history.error)}</ErrorText>}
          {history.data?.messages.map((turn, index) => <div className={"turn " + turn.role} key={index}>
            <div className="turn-text">{turn.message}</div>
            {turn.model_name && <small title="Language model used for this turn">Model · {turn.model_name}</small>}
            {!turn.model_name && Boolean(turn.model_usage?.model_failures) && <small>Model unavailable · Used local routing</small>}
            {!!turn.source_checks?.length && <div className="pills" aria-label="Job sources">
              {turn.source_checks.map(source => <span className="pill" key={source.name}>
                {source.name} · {{ checked: "Checked", cached: "Recent results", unavailable: "Unavailable",
                  refreshing: "Refreshing" }[source.status] ?? source.status}
              </span>)}
            </div>}
            {turn.external_links?.map(link => <Button variant="outline" asChild key={link.url}>
              <a href={link.url} target="_blank" rel="noreferrer">{link.label} <ArrowUpRight size={15}/></a>
            </Button>)}
            {turn.recommendations?.map(item => <JobCard key={item.job.id} job={item.job} match={item} onOpen={onOpen}/>)}
            {turn.jobs?.map(job => <JobCard key={job.id} job={job} onOpen={onOpen}/>)}
            {!!turn.trajectory?.length && <details className="tool-details"><summary>Agent activity</summary>
              {turn.trajectory.map((tool, index) => <div key={index}>{label(tool.name)} · {tool.status}</div>)}</details>}
          </div>)}
          {optimistic && <div className="turn user"><div className="turn-text">{optimistic}</div></div>}
          {send.isPending && <p className="loading">Working on your request…</p>}
          {send.isError && <ErrorText>{message(send.error)}</ErrorText>}
        </div>
        <form className="chat-form" onSubmit={submit}>
          <input aria-label="Message the career agent" value={input} onChange={event => setInput(event.target.value)}
            maxLength={2000} placeholder="Ask about your next role…"/>
          <Button aria-label="Send message" disabled={!input.trim() || send.isPending || Boolean(thread && history.isPending)}><Send size={18}/></Button>
        </form>
      </section>
    </div>
  </>;
}

const datasets = ["agent-single-turn", "agent-multiturn", "prompt-injection", "retrieval", "resume-extraction", "job-explanations"];

function Evaluations({ token }: { token: string }) {
  const cache = useQueryClient();
  const runs = useQuery({ queryKey: ["evaluations", token], queryFn: () => request<EvalRun[]>(token, "/evaluation/runs"),
    refetchInterval: query => query.state.data?.some(run => run.status === "queued" || run.status === "running") ? 2500 : false });
  const feedback = useQuery({ queryKey: ["review", token], queryFn: () => request<{ id: string; job_id: string; kind: string; created_at: string }[]>(token, "/evaluation/feedback") });
  const [selected, setSelected] = useState("");
  const [comparison, setComparison] = useState("");
  const [reviewCase, setReviewCase] = useState("");
  const cases = useQuery({ queryKey: ["evaluation-detail", token, selected], enabled: Boolean(selected),
    queryFn: () => request<{ results: { case_id: string; passed?: boolean; judge?: Record<string, {score: number | null; reason: string}>;
      human_reviews?: {verdict: string; rationale: string; at: string}[] }[] }>(token, "/evaluation/runs/" + selected) });
  const review = useMutation({
    mutationFn: (body: object) => request(token, "/evaluation/runs/" + selected + "/cases/" + reviewCase + "/review",
      { method: "POST", body: JSON.stringify(body) }),
    onSuccess: () => cache.invalidateQueries({ queryKey: ["evaluation-detail", token, selected] }),
  });
  const run = useMutation({ mutationFn: (body: object) => request(token, "/evaluation/runs", {
    method: "POST", body: JSON.stringify(body),
  }), onSuccess: () => cache.invalidateQueries({ queryKey: ["evaluations", token] }) });
  const active = runs.data?.find(item => item.id === selected) ?? runs.data?.find(item => item.status === "completed");
  const compare = runs.data?.find(item => item.id === comparison && item.dataset === active?.dataset);
  const scalar = active ? Object.entries(active.metrics).filter(([, value]) => typeof value === "number" || value === null) : [];
  const chart = active?.dataset === "careerlens-retrieval-v1" ? Object.entries(active.metrics).flatMap(([key, value]) =>
    typeof value === "object" && value !== null && typeof value.ndcg_at_10 === "number"
      ? [{ strategy: label(key), ndcg: value.ndcg_at_10, recall: value.recall_at_10 }] : []) : [];
  return <>
    <Heading title="Evaluations" copy="Inspect recorded runs, dataset versions, and offline quality metrics."/>
    <section className="panel content-panel">
      <h2>Queue evaluation</h2>
      <form className="eval-form" onSubmit={event => {
        event.preventDefault();
        const fields = new FormData(event.currentTarget);
        run.mutate({ dataset: fields.get("dataset"), label: fields.get("label"),
          prompt_label: fields.get("prompt") || null, judge_model: fields.get("judge") || null });
      }}>
        <label>Dataset<select name="dataset">{datasets.map(value => <option key={value} value={value}>{value} v1</option>)}</select></label>
        <label>Experiment label<input name="label" defaultValue="baseline" maxLength={80} required/></label>
        <label>Prompt label<input name="prompt" placeholder="Deployment default"/></label>
        <label>Judge model<input name="judge" placeholder="Optional DeepEval model"/></label>
        <Button disabled={run.isPending}>Queue run <ArrowRight size={16}/></Button>
      </form>
      {run.isError && <ErrorText>{message(run.error)}</ErrorText>}
      {run.isSuccess && <p className="success" role="status">Evaluation queued.</p>}
    </section>
    <div className="section-heading"><h2>Run history</h2></div>
    {runs.isError && <ErrorText>{message(runs.error)}</ErrorText>}
    {runs.data?.length === 0 && <Empty icon={<BarChart3 size={22}/>} title="No recorded runs" description="Queue an evaluation to see its measured results here."/>}
    {!!runs.data?.length && <div className="panel table-wrap"><table><thead><tr><th>Experiment</th><th>Dataset</th><th>Status</th><th>Started</th></tr></thead><tbody>
      {runs.data.map(item => <tr key={item.id}><td><button className="text-link" onClick={() => { setSelected(item.id); setComparison(""); setReviewCase(""); review.reset(); }}>{item.configuration.label}</button></td>
        <td>{item.dataset}</td><td><span className="pill">{item.status}</span></td><td>{formatDate(item.created_at)}</td></tr>)}
    </tbody></table></div>}
    {active && <section className="panel content-panel eval-results">
      <div className="section-heading compact"><h2>{active.configuration.label} results</h2>
        {active.finished_at && <span>Finished {formatDate(active.finished_at)}</span>}</div>
      <p>Dataset {active.dataset} · Prompt {active.configuration.prompt_label ?? "default"} · Model {active.configuration.model ?? "local"}</p>
      <label>Compare with<select value={comparison} onChange={event => setComparison(event.target.value)}>
        <option value="">Select a completed run</option>
        {runs.data?.filter(item => item.id !== active.id && item.dataset === active.dataset && item.status === "completed").map(item =>
          <option value={item.id} key={item.id}>{item.configuration.label} · {formatDate(item.created_at)}</option>)}
      </select></label>
      {chart.length > 0 && <div className="chart" aria-label="Retrieval NDCG and Recall at 10">
        <ResponsiveContainer width="100%" height={285}><BarChart data={chart} margin={{ left: 0, right: 20, top: 10 }}>
          <CartesianGrid vertical={false} strokeDasharray="4 4"/><XAxis dataKey="strategy"/><YAxis domain={[0, 1]}/><Tooltip/>
          <Bar dataKey="ndcg" name="NDCG@10" fill="#6043dd" radius={[5, 5, 0, 0]}/>
          <Bar dataKey="recall" name="Recall@10" fill="#2a8b78" radius={[5, 5, 0, 0]}/>
        </BarChart></ResponsiveContainer>
      </div>}
      {active.dataset === "careerlens-retrieval-v1" && <div className="table-wrap">
        <table><thead><tr><th>Strategy</th><th>Precision@5</th><th>Recall@10</th><th>NDCG@10</th><th>p95 ms</th></tr></thead><tbody>
          {Object.entries(active.metrics).flatMap(([key, value]) => value && typeof value === "object" ? [
            <tr key={key}><td>{label(key)}</td>{value.status ? <td colSpan={4}>{value.status}</td> :
              ["precision_at_5", "recall_at_10", "ndcg_at_10", "latency_p95_ms"].map(metric => {
                const baseline = compare?.metrics[key];
                const prior = baseline && typeof baseline === "object" ? baseline[metric] : null;
                return <td key={metric}>{typeof value[metric] === "number" ? value[metric].toLocaleString(undefined, { maximumFractionDigits: 3 }) : "—"}
                  {typeof prior === "number" && <small className="comparison">vs {prior.toFixed(3)}</small>}</td>;
              })}</tr>,
          ] : [])}
        </tbody></table>
      </div>}
      <div className="metric-grid">{scalar.map(([key, value]) => <div key={key}><span>{metricLabel(key)}</span>
        <strong>{typeof value === "number" ? value.toLocaleString(undefined, { maximumFractionDigits: 3 }) : "Unavailable"}</strong>
        {typeof compare?.metrics[key] === "number" && <small className="comparison">vs {Number(compare.metrics[key]).toFixed(3)}</small>}</div>)}</div>
      {cases.data?.results && <details className="case-details"><summary>Case results ({cases.data.results.length})</summary>
        {cases.data.results.map(item => <div key={item.case_id}>
          <button className="text-link" onClick={() => { setReviewCase(item.case_id); review.reset(); }}>{item.case_id}</button>
          {typeof item.passed === "boolean" && <span>{item.passed ? "Passed" : "Failed"}</span>}</div>)}</details>}
      {reviewCase && selected && <form className="form-stack review-form" key={reviewCase} onSubmit={event => {
        event.preventDefault();
        const fields = new FormData(event.currentTarget);
        review.mutate({ verdict: fields.get("verdict"), rationale: fields.get("rationale") });
      }}>
        <h3>Review {reviewCase}</h3>
        {Object.entries(cases.data?.results.find(item => item.case_id === reviewCase)?.judge ?? {}).map(([name, item]) =>
          <p key={name}>{metricLabel(name)} · {item.score ?? "Unavailable"} · {item.reason}</p>)}
        {cases.data?.results.find(item => item.case_id === reviewCase)?.human_reviews?.map((item, index) =>
          <p key={index}>{formatDate(item.at)} · {label(item.verdict)} · {item.rationale}</p>)}
        <label>Verdict<select name="verdict"><option value="supported">Supported</option><option value="unsupported">Unsupported</option>
          <option value="abstain">Abstain</option></select></label>
        <label>Rationale<textarea name="rationale" minLength={4} maxLength={1000} required/></label>
        <Button disabled={review.isPending}>Save review</Button>
        {review.isSuccess && <p className="success" role="status">Review recorded.</p>}
        {review.isError && <ErrorText>{message(review.error)}</ErrorText>}
      </form>}
    </section>}
    <div className="section-heading"><h2>Flagged recommendations</h2></div>
    <section className="panel content-panel">
      {feedback.isError && <ErrorText>{message(feedback.error)}</ErrorText>}
      {feedback.data?.length === 0 && <p>No feedback reports to review.</p>}
      {feedback.data?.map(item => <div className="resume-row" key={item.id}>
        <span>{label(item.kind)} · {formatDate(item.created_at)}</span><span className="feedback-id">{item.job_id}</span>
      </div>)}
    </section>
  </>;
}

function Operations({ token, langfuse }: { token: string; langfuse: string }) {
  const health = useQuery({ queryKey: ["operation-health", token], queryFn: () => request<{ status: string }>(token, "/ready"),
    retry: false });
  return <><Heading title="Observability" copy="Application readiness and Langfuse traces."/>
    <section className="panel content-panel"><h2>Application readiness</h2>
      <p role="status">{health.isPending ? "Checking…" : health.data ? "Database and queue are ready." : "The application reported a readiness error."}</p>
    </section>
    <section className="panel content-panel">
      <span className="agent-glyph"><BarChart3 size={20}/></span>
      <h2>Langfuse</h2><p>Agent traces, model calls, and experiments</p>
      {/^https?:\/\//.test(langfuse) ? <Button variant="outline" asChild>
        <a href={langfuse} rel="noreferrer" target="_blank">Open Langfuse <ArrowUpRight size={15}/></a>
      </Button> : <small>No Langfuse URL configured.</small>}
    </section>
  </>;
}

function AppShell({ token, account, onLogout, langfuse }: {
  token: string; account: Account; onLogout: () => void; langfuse: string;
}) {
  const [view, setView] = useState<View>("Dashboard");
  const [detail, setDetail] = useState<Detail>();
  const [menu, setMenu] = useState(false);
  const links = [...nav, ...(account.admin ? [
    { key: "Evaluations", icon: BarChart3 } as const, { key: "Observability", icon: Layers3 } as const,
  ] : [])];
  function go(next: View) { setView(next); setMenu(false); window.scrollTo(0, 0); }
  return <div className="app-shell">
    {menu && <button className="mobile-backdrop" onClick={() => setMenu(false)} aria-label="Close navigation"/>}
    <aside className={"sidebar " + (menu ? "opened" : "")}><Brand onClick={() => go("Dashboard")}/>
      <nav aria-label="Workspace">
        <small>WORKSPACE</small>
        {links.map(({ key, icon: Icon }) => <button key={key} className={view === key ? "active" : ""}
          aria-current={view === key ? "page" : undefined} onClick={() => go(key)}><Icon size={18}/><span>{key}</span></button>)}
      </nav>
      <div className="sidebar-bottom">
        <div className="account-chip"><span className="avatar">{account.email.slice(0, 2).toUpperCase()}</span>
          <span><strong>{account.email.split("@")[0]}</strong><small>{account.admin ? "Administrator" : "Candidate"}</small></span>
        </div>
        <button className="sign-out" onClick={onLogout}><LogOut size={16}/> Sign out</button>
      </div>
    </aside>
    <div className="main-area">
      <header className="topbar">
        <div><button className="menu-toggle" onClick={() => setMenu(!menu)} aria-label="Open navigation"><Menu size={23}/></button>
          <span>Workspace</span><ChevronRight size={14}/><strong>{view}</strong></div>
        <button className="topbar-profile" onClick={() => go("Resume & profile")} aria-label="Open candidate profile">
          <span className="avatar">{account.email.slice(0, 2).toUpperCase()}</span></button>
      </header>
      <main className="main-content" id="workspace">
        {view === "Dashboard" && <Dashboard token={token} account={account} go={go} onOpen={setDetail}/>}
        {view === "Discover jobs" && <Discover token={token} onOpen={setDetail}/>}
        {view === "Saved jobs" && <SavedJobs token={token} onOpen={setDetail}/>}
        {view === "Resume & profile" && <ResumeProfile token={token}/>}
        {view === "Career agent" && <Agent token={token} onOpen={setDetail}/>}
        {view === "Evaluations" && account.admin && <Evaluations token={token}/>}
        {view === "Observability" && account.admin && <Operations token={token} langfuse={langfuse}/>}
      </main>
    </div>
    {detail && <JobDetail key={detail.job.id} detail={detail} token={token} onClose={() => setDetail(undefined)}/>}
  </div>;
}

const getSession = () => sessionStorage.getItem("careerlens.session") ?? "";
const noSession = () => "";
function subscribeSession(onStoreChange: () => void) {
  window.addEventListener("careerlens:session", onStoreChange);
  return () => window.removeEventListener("careerlens:session", onStoreChange);
}

function Session({ langfuse }: { langfuse: string }) {
  const token = useSyncExternalStore(subscribeSession, getSession, noSession);
  const [mode, setMode] = useState<"login" | "register" | null>(null);
  const cache = useQueryClient();
  const account = useQuery({ queryKey: ["account", token], queryFn: () => request<Account>(token, "/auth/me"),
    enabled: Boolean(token), retry: false });
  function remember(value: string) {
    sessionStorage.setItem("careerlens.session", value);
    window.dispatchEvent(new Event("careerlens:session"));
  }
  function logout() {
    sessionStorage.removeItem("careerlens.session");
    window.dispatchEvent(new Event("careerlens:session"));
    setMode(null);
    cache.clear();
  }
  if (token && account.isPending) return <main className="session-loading"><Brand/><p>Opening your workspace…</p></main>;
  if (token && account.isError) return <main className="session-loading"><CircleHelp size={28}/><ErrorText>{message(account.error)}</ErrorText>
    <div className="button-row"><Button onClick={() => void account.refetch()}>Retry</Button>
    <Button variant="outline" onClick={logout}>Return home</Button></div></main>;
  if (token && account.data) return <AppShell token={token} account={account.data} onLogout={logout} langfuse={langfuse}/>;
  if (mode) return <AuthForm mode={mode} setMode={setMode} onToken={remember}/>;
  return <Landing onAuth={setMode}/>;
}

export function CareerWorkspace(props: { langfuse: string }) {
  const [client] = useState(() => new QueryClient({ defaultOptions: { queries: { staleTime: 15000, refetchOnWindowFocus: false } } }));
  return <QueryClientProvider client={client}><Session {...props}/></QueryClientProvider>;
}

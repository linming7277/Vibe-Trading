import { useEffect, useState } from "react";
import { Link, useParams } from "react-router";
import { ArrowLeft } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, type ResearchReport } from "@/lib/api";
import { EmptyState, LoadingState, PageHeader, SourceBadge, WorkspacePage } from "@/components/workspace/WorkspaceUI";

export function ReportDetail() {
  const { reportId = "" } = useParams(); const [report, setReport] = useState<ResearchReport | null>(null); const [loading, setLoading] = useState(true);
  useEffect(() => { api.getResearchReport(reportId).then(setReport).catch(() => setReport(null)).finally(() => setLoading(false)); }, [reportId]);
  if (loading) return <WorkspacePage><LoadingState label="正在读取报告…" /></WorkspacePage>;
  if (!report) return <WorkspacePage><EmptyState title="报告不存在" /></WorkspacePage>;
  return <WorkspacePage className="max-w-5xl"><Link to="/reports" className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"><ArrowLeft className="h-4 w-4" />返回报告库</Link><PageHeader eyebrow={report.report_type} title={report.title} description={report.summary} actions={<SourceBadge status={report.source_status} asOf={report.data_as_of} />} /><article className="prose prose-slate max-w-none rounded-xl border bg-card p-6 shadow-sm dark:prose-invert md:p-10"><ReactMarkdown remarkPlugins={[remarkGfm]}>{report.content_md}</ReactMarkdown></article></WorkspacePage>;
}

/**
 * 行情页右侧 300px 信息面板：报价 / 分析 / 基本面 三个 Tab。
 */
import { AlertTriangle } from "lucide-react"
import type { ApiState } from "@/api/client"
import type {
  AnalysisComponentScore,
  AnalysisResult,
  FundamentalSnapshotField,
  FundamentalSnapshotResponse,
  Quote,
  ResearchEvidenceDimension,
  ResearchInvalidation,
  ResearchKeyLevel,
} from "@/api/types"
import {
  formatFundamentalNormalized,
  fundamentalFieldLabel,
  fundamentalGroupForField,
  FUNDAMENTAL_GROUP_LABELS,
  FUNDAMENTAL_GROUP_ORDER,
} from "@/api/shared"
import { fmtLarge, fmtPct, fmtPrice, fmtTime, trendClass } from "@/lib/fmt"
import { DataModeBadge } from "@/components/terminal/DataModeBadge"
import { Stat } from "@/components/terminal/Stat"
import { EmptyState, ErrorState, LoadingState } from "@/components/terminal/States"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { cn } from "@/lib/utils"

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="mt-3 first:mt-0">
      <div className="mb-1 text-[11px] font-medium text-futu-orange">{title}</div>
      {children}
    </div>
  )
}

function TabBody<T>({
  state,
  loadingText,
  children,
}: {
  state: ApiState<T>
  loadingText: string
  children: (data: T) => React.ReactNode
}) {
  if (state.error && !state.data)
    return <ErrorState error={state.error} onRetry={state.reload} />
  if (state.loading && !state.data) return <LoadingState text={loadingText} />
  if (!state.data) return <EmptyState />
  return <>{children(state.data)}</>
}

// ── 报价 Tab ───────────────────────────────────────────────────
function QuoteTab({ quote }: { quote: Quote }) {
  return (
    <div>
      <Section title="估值与规模">
        <Stat label="市盈率 PE" value={quote.pe === null ? "--" : quote.pe.toFixed(2)} />
        <Stat label="市净率 PB" value={quote.pb === null ? "--" : quote.pb.toFixed(2)} />
        <Stat label="总市值" value={fmtLarge(quote.marketCap)} />
      </Section>
      <Section title="交易状态">
        <Stat
          label="换手率"
          value={quote.turnover === null ? "--" : fmtPct(quote.turnover)}
        />
        <Stat
          label="振幅"
          value={quote.amplitude === null ? "--" : fmtPct(quote.amplitude)}
        />
      </Section>
      <Section title="52周区间">
        <Stat
          label="52周最高"
          value={fmtPrice(quote.high52w)}
          valueClassName={trendClass(
            quote.high52w !== null ? quote.high52w - quote.prevClose : null
          )}
        />
        <Stat
          label="52周最低"
          value={fmtPrice(quote.low52w)}
          valueClassName={trendClass(
            quote.low52w !== null ? quote.low52w - quote.prevClose : null
          )}
        />
      </Section>
      <Section title="数据证据">
        <Stat label="提供方" value={quote.provenance.provider} />
        <Stat
          label="时间模式"
          value={quote.provenance.temporalMode === "latest" ? "最新快照" : "点时快照"}
        />
        <Stat
          label="模式 / 质量"
          value={
            <span className="flex items-center gap-1">
              <DataModeBadge mode={quote.dataMode} />
              <span
                className={
                  quote.quality === "valid" ? "text-futu-teal" : "text-futu-gold"
                }
              >
                {quote.quality}
              </span>
            </span>
          }
        />
        <Stat label="会话状态" value={quote.sessionStatus} />
        <Stat
          label="交易状态核验"
          value={quote.provenance.tradeStatusVerified ? "已核验" : "未核验"}
          valueClassName={
            quote.provenance.tradeStatusVerified ? "text-futu-teal" : "text-futu-gold"
          }
        />
        <Stat
          label="正式用途"
          value={quote.formalUseEligible ? "可用" : "仅展示，不用于评分/预警"}
          valueClassName={
            quote.formalUseEligible ? "text-futu-teal" : "text-futu-gold"
          }
        />
        <Stat label="行情时间" value={fmtTime(quote.provenance.dataTime)} />
        {quote.provenance.requestedAt && (
          <Stat label="请求时间" value={fmtTime(quote.provenance.requestedAt)} />
        )}
        <Stat label="权威快照时点" value={fmtTime(quote.provenance.asOf)} />
        <Stat
          label="Provider更新时间"
          value={fmtTime(quote.provenance.providerUpdatedAt)}
        />
        <Stat label="抓取时间" value={fmtTime(quote.provenance.fetchedAt)} />
        <Stat
          label="原始新鲜度"
          value={`${Math.round(quote.provenance.freshnessSeconds)} 秒`}
        />
        <Stat label="成交额" value={fmtLarge(quote.amount)} />
        <div className="mt-2 border-t border-futu-divider pt-2 text-[10px] leading-4 text-futu-dim">
          <div>来源链：{quote.provenance.sourceChain.join(" → ")}</div>
          {quote.provenance.warnings.map((warning, index) => (
            <div
              key={`quote-warning-${index}`}
              className="mt-1 text-futu-gold"
            >
              注意：{warning}
            </div>
          ))}
        </div>
      </Section>
      <Section title="兼容字段说明">
        <p className="text-[10px] leading-4 text-futu-dim">
          52周高低点不属于本次原生快照字段，当前不再从日线或模拟数据回填。
        </p>
      </Section>
    </div>
  )
}

// ── 分析 Tab ───────────────────────────────────────────────────
function scoreColor(score: number): string {
  if (score >= 70) return "text-futu-up"
  if (score >= 50) return "text-futu-gold"
  return "text-futu-down"
}

function ComponentRow({ component }: { component: AnalysisComponentScore }) {
  return (
    <div
      className={cn(
        "border-b border-futu-divider py-1.5 last:border-0",
        !component.enabled && "opacity-55"
      )}
    >
      <div className="flex items-center justify-between gap-2 text-[10px]">
        <span className="min-w-0 truncate text-futu-sub">
          {component.label}
          {!component.enabled && "（已禁用）"}
        </span>
        <span className={cn("tnum shrink-0", scoreColor(component.score))}>
          {component.score.toFixed(1)} · 权重 {(component.weight * 100).toFixed(0)}%
        </span>
      </div>
      {component.evidence.map((item, index) => (
        <p key={`${component.key}-evidence-${index}`} className="mt-1 text-[10px] leading-4 text-futu-dim">
          · {item}
        </p>
      ))}
      {component.warnings.map((warning, index) => (
        <p key={`${component.key}-warning-${index}`} className="mt-1 text-[10px] leading-4 text-futu-gold">
          注意：{warning}
        </p>
      ))}
    </div>
  )
}

function EvidenceDimensionRow({ item }: { item: ResearchEvidenceDimension }) {
  return (
    <div className="border-b border-futu-divider py-1.5 last:border-0">
      <div className="flex items-center justify-between gap-2">
        <span className="text-[11px] text-futu-sub">{item.label}</span>
        {item.score !== null && (
          <span className={cn("tnum text-[10px]", scoreColor(item.score))}>
            子证据 {item.score.toFixed(1)}
          </span>
        )}
      </div>
      <p className="mt-0.5 text-[10px] leading-4 text-futu-dim">{item.summary}</p>
    </div>
  )
}

function KeyLevelRow({ level }: { level: ResearchKeyLevel }) {
  const normalizedKind = level.kind.toLowerCase()
  const isSupport = normalizedKind === "support"
  const isResistance = normalizedKind === "resistance"
  const label = isSupport ? "支撑" : isResistance ? "压力" : level.kind
  return (
    <div className="border-b border-futu-divider py-1.5 last:border-0">
      <div className="flex items-center justify-between gap-2">
        <span className="flex min-w-0 items-center gap-1.5">
          <span
            className={cn(
              "rounded-sm px-1 text-[10px]",
              isSupport
                ? "bg-futu-down/10 text-futu-down"
                : isResistance
                  ? "bg-futu-up/10 text-futu-up"
                  : "bg-futu-elevated text-futu-sub"
            )}
          >
            {label}
          </span>
          <span className="text-[10px] text-futu-dim">
            {level.status === "confirmed" ? "已确认" : "暂定"}
          </span>
        </span>
        <span className="tnum shrink-0 text-xs font-medium text-futu-text">
          {fmtPrice(level.price)}
        </span>
      </div>
      <p className="mt-0.5 text-[10px] leading-4 text-futu-dim">
        来源：{level.provenance}
      </p>
    </div>
  )
}

function InvalidationRow({ item }: { item: ResearchInvalidation }) {
  return (
    <div className="border-b border-futu-divider py-1.5 last:border-0">
      <div className="flex items-start justify-between gap-2">
        <span className="text-[11px] leading-4 text-futu-sub">{item.description}</span>
        {item.price !== null && (
          <span className="tnum shrink-0 text-[11px] text-futu-down">
            {fmtPrice(item.price)}
          </span>
        )}
      </div>
      <p className="mt-0.5 text-[10px] leading-4 text-futu-dim">
        确认：{item.confirmation}
      </p>
    </div>
  )
}

function AnalysisTab({ a }: { a: AnalysisResult }) {
  return (
    <div>
      {/* 顶层只展示共享快照 evidence_score；不是概率。 */}
      <div className="flex items-center gap-3 rounded border border-futu-divider bg-futu-elevated/50 px-3 py-2">
        <span
          className={cn(
            "tnum text-3xl font-bold leading-none",
            scoreColor(a.evidenceScore)
          )}
        >
          {a.evidenceScore.toFixed(0)}
        </span>
        <div className="min-w-0">
          <span className="rounded-sm border border-futu-orange/40 bg-futu-orange/10 px-1.5 py-px text-[11px] text-futu-orange">
            {a.rating}
          </span>
          <div className="mt-1 text-[10px] text-futu-dim">未校准证据分 / 100</div>
        </div>
        <DataModeBadge mode={a.dataMode} className="ml-auto" />
      </div>
      <p className="mt-1 text-[10px] leading-4 text-futu-gold">
        该分数用于汇总证据，不代表上涨概率或收益承诺。
      </p>
      {a.summary && (
        <p className="mt-2 text-[11px] leading-relaxed text-futu-sub">{a.summary}</p>
      )}

      <Section title="数据血缘">
        <div className="rounded border border-futu-divider bg-futu-elevated/30 px-2 py-1">
          <Stat label="提供方" value={a.dataRef.provider} />
          <Stat label="最后K线" value={fmtTime(a.dataRef.lastBarAt)} />
          <Stat label="抓取时间" value={fmtTime(a.dataRef.fetchedAt)} />
          <Stat label="质量 / 复权" value={`${a.dataRef.quality} / ${a.dataRef.adjustment}`} />
          <Stat label="K线数 / 时区" value={`${a.dataRef.barCount} / ${a.dataRef.timezone}`} />
          <p className="mt-1 break-words border-t border-futu-divider pt-1 text-[10px] leading-4 text-futu-dim">
            来源链：{a.dataRef.sourceChain.length > 0 ? a.dataRef.sourceChain.join(" → ") : "未提供"}
          </p>
        </div>
      </Section>

      {a.componentScores.length > 0 && (
        <Section title="评分构成">
          {a.componentScores.map((component) => (
            <ComponentRow key={component.key} component={component} />
          ))}
        </Section>
      )}

      {a.evidenceDimensions.length > 0 && (
        <Section title={`技术证据维度 · ${a.sectionStatus.technicalSummary}`}>
          {a.evidenceDimensions.map((item) => (
            <EvidenceDimensionRow key={item.key} item={item} />
          ))}
        </Section>
      )}

      {a.keyLevels.length > 0 && (
        <Section title="关键价位">
          {a.keyLevels.map((level, index) => (
            <KeyLevelRow
              key={`${level.kind}-${level.price}-${index}`}
              level={level}
            />
          ))}
        </Section>
      )}

      {a.invalidations.length > 0 && (
        <Section title="失效条件">
          <div className="rounded border border-futu-down/35 bg-futu-down/5 px-2">
            {a.invalidations.map((item, index) => (
              <InvalidationRow key={`${item.description}-${index}`} item={item} />
            ))}
          </div>
        </Section>
      )}

      {a.scoreWarnings.length > 0 && (
        <Section title="警告与边界">
          <div className="rounded border border-futu-gold/35 bg-futu-gold/5 px-2 py-1">
            {a.scoreWarnings.map((warning, index) => (
              <div key={`${warning}-${index}`} className="flex items-start gap-1.5 py-1">
                <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0 text-futu-gold" />
                <span className="text-[10px] leading-4 text-futu-sub">{warning}</span>
              </div>
            ))}
          </div>
        </Section>
      )}

      <Section title="快照审计">
        <Stat label="决策状态" value={`${a.rating} / ${a.barStatus === "confirmed" ? "已确认" : "暂定"}`} />
        <Stat label="快照时间" value={fmtTime(a.asOf)} />
        <Stat label="引擎 / 配置" value={`${a.engineVersion} / ${a.configVersion}`} />
        <p className="mt-1 break-all text-[9px] leading-3 text-futu-dim">
          Snapshot {a.snapshotId}
        </p>
      </Section>
    </div>
  )
}

// ── 基本面 Tab ─────────────────────────────────────────────────
const FUND_GROUPS = FUNDAMENTAL_GROUP_ORDER

function CompactFundamentalField({ field }: { field: FundamentalSnapshotField }) {
  return (
    <div className="border-b border-futu-divider py-1.5 last:border-0">
      <div className="flex items-start justify-between gap-2">
        <span className="min-w-0 text-[10px] text-futu-sub">
          {fundamentalFieldLabel(field.name)}
          <span className={cn(
            "ml-1 rounded-sm px-1 py-px text-[8px]",
            field.status === "verified"
              ? "bg-futu-teal/10 text-futu-teal"
              : "bg-futu-gold/10 text-futu-gold"
          )}>
            {field.status === "verified" ? "已核验" : "未核验"}
          </span>
        </span>
        <span className="tnum shrink-0 text-right text-[11px] text-futu-text">
          {formatFundamentalNormalized(field)}
        </span>
      </div>
      <p className="mt-0.5 break-all text-[8px] leading-3 text-futu-dim">
        {field.basis} · {field.source_field}
      </p>
      <p className="mt-0.5 text-[8px] leading-3 text-futu-dim">
        报告期 {field.period_end ? fmtTime(field.period_end) : "—"}<br />
        发布 {field.published_at ? fmtTime(field.published_at) : "—"} · 市场 {field.market_time ? fmtTime(field.market_time) : "—"}
      </p>
    </div>
  )
}

function FundamentalTab({ f }: { f: FundamentalSnapshotResponse }) {
  const companyName = f.data.fields.find((field) => field.name === "name")?.normalized_value
  const industry = f.data.fields.find((field) => field.name === "industry")?.normalized_value
  const known = FUND_GROUPS.map((group) => ({
    group,
    fields: f.data.fields.filter((field) => fundamentalGroupForField(field.name) === group),
  })).filter((entry) => entry.fields.length > 0)
  return (
    <div>
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium text-futu-text">
          {typeof companyName === "string" ? companyName : f.instrument.symbol}
        </span>
        <span className="flex items-center gap-1">
          <DataModeBadge mode={f.data.mode} />
          {typeof industry === "string" && (
            <span className="rounded-sm bg-futu-elevated px-1.5 py-px text-[10px] text-futu-sub">
              {industry}
            </span>
          )}
        </span>
      </div>
      <div className="mt-2 rounded border border-futu-divider bg-futu-elevated/30 px-2 py-1 text-[9px] leading-4 text-futu-dim">
        <div>提供方 {f.data.provider} · {f.data.currency} · {f.data.timezone}</div>
        <div>数据时点 {fmtTime(f.data.data_time)}</div>
        <div>
          覆盖 {f.coverage.verified_financial_fields.length} 个已核验财报字段 ·{" "}
          {f.coverage.passed ? "正式筛选可用" : "仅展示，不参与正式筛选"}
        </div>
      </div>
      {known.map((entry) => (
        <Section key={entry.group} title={FUNDAMENTAL_GROUP_LABELS[entry.group]}>
          {entry.fields.map((field) => (
            <CompactFundamentalField key={field.name} field={field} />
          ))}
        </Section>
      ))}
      {f.data.warnings.length > 0 && (
        <Section title="警告与边界">
          {f.data.warnings.map((warning, index) => (
            <p key={`${warning}:${index}`} className="mb-1 text-[9px] leading-4 text-futu-gold">
              · {warning}
            </p>
          ))}
        </Section>
      )}
    </div>
  )
}

// ── 面板 ───────────────────────────────────────────────────────
export function RightPanel({
  quote,
  analysis,
  fundamental,
  fundamentalApplicable,
}: {
  quote: ApiState<Quote>
  analysis: ApiState<AnalysisResult>
  fundamental: ApiState<FundamentalSnapshotResponse>
  fundamentalApplicable: boolean
}) {
  return (
    <aside className="flex w-[300px] shrink-0 flex-col border-l border-futu-border bg-futu-panel">
      <Tabs defaultValue="quote" className="flex min-h-0 flex-1 flex-col gap-0">
        <TabsList className="grid h-9 w-full shrink-0 grid-cols-3 rounded-none border-b border-futu-divider bg-futu-panel p-0">
          {[
            { value: "quote", label: "报价" },
            { value: "analysis", label: "分析" },
            { value: "fundamental", label: "基本面" },
          ].map((t) => (
            <TabsTrigger
              key={t.value}
              value={t.value}
              className="h-full rounded-none border-0 border-b-2 border-transparent text-xs text-futu-sub shadow-none data-[state=active]:border-futu-orange data-[state=active]:bg-transparent data-[state=active]:font-medium data-[state=active]:text-futu-orange data-[state=active]:shadow-none"
            >
              {t.label}
            </TabsTrigger>
          ))}
        </TabsList>
        <TabsContent
          value="quote"
          className="min-h-0 flex-1 overflow-y-auto px-3 py-2 data-[state=inactive]:hidden"
        >
          <TabBody state={quote} loadingText="报价加载中…">
            {(q) => <QuoteTab quote={q} />}
          </TabBody>
        </TabsContent>
        <TabsContent
          value="analysis"
          className="min-h-0 flex-1 overflow-y-auto px-3 py-2 data-[state=inactive]:hidden"
        >
          <TabBody state={analysis} loadingText="分析加载中…">
            {(a) => <AnalysisTab a={a} />}
          </TabBody>
        </TabsContent>
        <TabsContent
          value="fundamental"
          className="min-h-0 flex-1 overflow-y-auto px-3 py-2 data-[state=inactive]:hidden"
        >
          {fundamentalApplicable ? (
            <>
              <TabBody state={fundamental} loadingText="基本面加载中…">
                {(f) => <FundamentalTab f={f} />}
              </TabBody>
            </>
          ) : (
            <div className="rounded border border-futu-divider bg-futu-elevated/40 px-3 py-3 text-[10px] leading-relaxed text-futu-sub">
              <div className="font-medium text-futu-text">板块指数不适用发行人基本面</div>
              <p className="mt-1 text-futu-dim">
                板块没有单一发行人的财务报表。本页不发起基本面快照请求，行情、技术结构和板块点位继续正常使用。
              </p>
            </div>
          )}
        </TabsContent>
      </Tabs>
    </aside>
  )
}

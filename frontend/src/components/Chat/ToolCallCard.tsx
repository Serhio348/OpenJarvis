import { useState } from 'react';
import { ChevronDown, ChevronRight, Loader2, CheckCircle2, XCircle } from 'lucide-react';
import type { ToolCallInfo } from '../../types';

interface Props {
  toolCall: ToolCallInfo;
}

const statusConfig = {
  running: { icon: Loader2, color: 'var(--color-accent)' },
  success: { icon: CheckCircle2, color: 'var(--color-success)' },
  error: { icon: XCircle, color: 'var(--color-error)' },
};

function asText(value: unknown): string {
  if (value == null) return '';
  if (typeof value === 'string') return value;
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function previewArgs(raw: unknown): string {
  const text = asText(raw);
  if (!text) return '';
  try {
    const parsed = typeof raw === 'object' && raw !== null ? raw : JSON.parse(text);
    if (parsed && typeof parsed === 'object') {
      const entries = Object.entries(parsed as Record<string, unknown>);
      if (entries.length === 0) return '';
      const [k, v] = entries[0];
      const valStr = typeof v === 'string' ? v : JSON.stringify(v);
      const trimmed = valStr.length > 40 ? `${valStr.slice(0, 40)}…` : valStr;
      return entries.length === 1 ? `${k}: ${trimmed}` : `${k}: ${trimmed}, …`;
    }
  } catch {
    /* fall through */
  }
  return text.length > 60 ? `${text.slice(0, 60)}…` : text;
}

export function ToolCallCard({ toolCall }: Props) {
  const [expanded, setExpanded] = useState(false);
  const config = statusConfig[toolCall.status];
  const StatusIcon = config.icon;
  const preview = previewArgs(toolCall.arguments);

  return (
    <div
      className="rounded-md text-xs overflow-hidden"
      style={{
        border: '1px solid var(--color-border-subtle, var(--color-border))',
        background: 'var(--color-bg-tertiary, var(--color-bg-secondary))',
        fontFamily:
          'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace',
      }}
    >
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-2 w-full px-2.5 py-1.5 cursor-pointer text-left"
        style={{ background: 'transparent' }}
      >
        {expanded ? (
          <ChevronDown size={11} style={{ color: 'var(--color-text-tertiary)', flexShrink: 0 }} />
        ) : (
          <ChevronRight size={11} style={{ color: 'var(--color-text-tertiary)', flexShrink: 0 }} />
        )}
        <StatusIcon
          size={11}
          style={{ color: config.color, flexShrink: 0 }}
          className={toolCall.status === 'running' ? 'animate-spin' : ''}
        />
        <span
          style={{ color: 'var(--color-text)', fontWeight: 500, flexShrink: 0 }}
        >
          {toolCall.tool}
        </span>
        {preview && !expanded && (
          <span
            className="truncate"
            style={{ color: 'var(--color-text-tertiary)', fontSize: 10.5 }}
          >
            {preview}
          </span>
        )}
        <div className="flex-1" />
        {toolCall.latency != null && (
          <span
            style={{
              color: 'var(--color-text-tertiary)',
              fontSize: 10,
              flexShrink: 0,
            }}
          >
            {toolCall.latency < 1000
              ? `${Math.round(toolCall.latency)}ms`
              : `${(toolCall.latency / 1000).toFixed(1)}s`}
          </span>
        )}
      </button>
      {expanded && (
        <div
          className="px-2.5 pb-2 pt-0.5"
          style={{ borderTop: '1px solid var(--color-border-subtle, var(--color-border))' }}
        >
          {toolCall.arguments && (
            <div className="mt-1.5">
              <div
                style={{
                  color: 'var(--color-text-tertiary)',
                  fontSize: 9.5,
                  textTransform: 'uppercase',
                  letterSpacing: '0.05em',
                  marginBottom: 3,
                }}
              >
                args
              </div>
              <pre
                className="p-1.5 rounded overflow-auto"
                style={{
                  background: 'var(--color-code-bg, rgba(0,0,0,0.2))',
                  color: 'var(--color-text-secondary)',
                  fontSize: 11,
                  lineHeight: 1.4,
                  maxHeight: 120,
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-all',
                }}
              >
                {formatJson(toolCall.arguments)}
              </pre>
            </div>
          )}
          {toolCall.result != null && toolCall.result !== '' && (
            <div className="mt-1.5">
              <div
                style={{
                  color: 'var(--color-text-tertiary)',
                  fontSize: 9.5,
                  textTransform: 'uppercase',
                  letterSpacing: '0.05em',
                  marginBottom: 3,
                }}
              >
                result
              </div>
              <pre
                className="p-1.5 rounded overflow-auto"
                style={{
                  background: 'var(--color-code-bg, rgba(0,0,0,0.2))',
                  color: 'var(--color-text-secondary)',
                  fontSize: 11,
                  lineHeight: 1.4,
                  maxHeight: 180,
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-word',
                }}
              >
                {asText(toolCall.result)}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function formatJson(raw: unknown): string {
  if (typeof raw === 'object' && raw !== null) {
    try {
      return JSON.stringify(raw, null, 2);
    } catch {
      return String(raw);
    }
  }
  const text = asText(raw);
  try {
    return JSON.stringify(JSON.parse(text), null, 2);
  } catch {
    return text;
  }
}

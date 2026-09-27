import { Text, View } from "react-native";
import type { ConversationSegment } from "../../shared/conversation/turnAdapter";
import type { ShellRoute } from "../../shared/shell/shellRoutes";
import { useShellTheme } from "../../shared/shell/shellTheme";
import { assistantResponseStyles } from "./chatStyles";
import { ResultCard } from "./ResultCard";

export function AssistantResponse({ content, segments = [], onNavigate }: {
  content: string;
  segments?: readonly ConversationSegment[];
  onNavigate?: (route: ShellRoute) => void;
}) {
  const { palette } = useShellTheme();
  return (
    <View accessibilityLabel="Gideon response" style={assistantResponseStyles.container}>
      <Text selectable style={[assistantResponseStyles.text, { color: palette.text }]}>
        {content}
      </Text>
      {segments.filter(segment => segment.kind !== "text").map(segment => {
        if (segment.kind === "result") return <ResultCard key={segment.id} id={segment.id}
          producer={segment.producerKind ? `${segment.producerKind}${segment.producerId ? ` · ${segment.producerId}` : ""}` : segment.producerId}
          status={segment.status} title={segment.title} summary={segment.summary} record={segment.record} onOpen={onNavigate} />;
        const detail = segment.kind === "tool" ? `${segment.tool} · ${segment.lifecycle}${segment.status ? ` · ${segment.status}` : ""}`
          : segment.kind === "approval" ? `${segment.resolved ? `Approval ${segment.resolved}` : "Approval required"} · ${segment.tool}`
            : segment.kind === "activity" ? segment.text
              : segment.kind === "thinking" ? segment.text
                : segment.kind === "error" ? segment.text
                  : segment.kind === "citation" ? `${segment.label}${segment.excerpt ? ` — ${segment.excerpt}` : ""}`
                    : segment.kind === "file" ? `${segment.name}${segment.mediaType ? ` · ${segment.mediaType}` : ""}` : "";
        if (!detail) return null;
        const label = segment.kind === "tool" ? "Tool" : segment.kind === "approval" ? "Approval"
          : segment.kind === "activity" ? "Activity" : segment.kind === "thinking" ? "Reasoning"
            : segment.kind === "error" ? "Error" : segment.kind === "citation" ? "Citation" : "File";
        return <View key={segment.id} nativeID={`gideon-segment-${segment.id}`} accessibilityLabel={`${label} segment`}
          style={{ minWidth: 0, maxWidth: "100%", paddingVertical: 7, gap: 4 }}>
          <Text style={{ color: palette.muted, fontSize: 11, fontWeight: "700", textTransform: "uppercase" }}>{label}{segment.status ? ` · ${segment.status}` : ""}</Text>
          <Text selectable style={{ color: palette.text, fontSize: 14, lineHeight: 21, flexShrink: 1 }}>{detail}</Text>
        </View>;
      })}
    </View>
  );
}

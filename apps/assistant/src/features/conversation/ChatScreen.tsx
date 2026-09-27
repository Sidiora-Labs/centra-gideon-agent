import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { KeyboardAvoidingView, Platform, Pressable, ScrollView, Text, View } from "react-native";
import type { OwnerScope } from "../../shared/auth.web";
import { ConversationController } from "../../shared/conversation/controller";
import type { ConversationMessage } from "../../shared/conversation/types";
import type { ShellReturnContext } from "../../shared/shell/shellRoutes";
import type { ShellRoute } from "../../shared/shell/shellRoutes";
import { useShellTheme } from "../../shared/shell/shellTheme";
import { AssistantResponse } from "./AssistantResponse";
import { Composer } from "./Composer";
import { chatStyles } from "./chatStyles";
import { adaptConversationMessage } from "../../shared/conversation/turnAdapter";

export type ChatScreenProps = {
  controller: ConversationController;
  scope: OwnerScope;
  sessionId?: string;
  returnTo?: ShellReturnContext;
  onReturn?: () => void;
  scrollY?: number;
  onScrollChange?: (scrollY: number) => void;
  selectedMessageId?: string;
  navigate?: (route: ShellRoute) => void;
};

const suggestions = [
  "Help me plan my day",
  "Summarize a document",
  "Help me think through a decision",
];
const rememberedScroll = new Map<string, number>();
const SCROLL_MEMORY_LIMIT = 32;

function scrollKey(scope: OwnerScope, sessionId: string | null): string {
  return `${scope.cacheKey}\u0000${sessionId ?? "new"}`;
}

function isVisibleMessage(message: ConversationMessage): boolean {
  return message.role === "user" || message.role === "assistant" || message.role === "streaming";
}

export function ChatScreen({
  controller,
  scope,
  sessionId,
  returnTo,
  onReturn,
  scrollY,
  onScrollChange,
  selectedMessageId,
  navigate,
}: ChatScreenProps) {
  const { palette } = useShellTheme();
  const snapshot = useSyncExternalStore(controller.subscribe, controller.snapshot, controller.snapshot);
  const ownerMismatch = snapshot.scope?.cacheKey !== scope.cacheKey;
  const sessionMismatch = Boolean(sessionId && snapshot.sessionId !== sessionId);
  const contextMismatch = ownerMismatch || sessionMismatch;
  const state = contextMismatch ? {
    ...snapshot,
    scope: null,
    sessionId: sessionId ?? null,
    title: "",
    messages: [],
    draft: "",
    phase: "loading" as const,
    connected: false,
    running: false,
    error: "",
  } : snapshot;
  const list = useRef<ScrollView>(null);
  const followLatest = useRef(true);
  const [awayFromLatest, setAwayFromLatest] = useState(false);
  const activeSessionId = state.sessionId;
  const messages = useMemo(() => state.messages.filter(message =>
    isVisibleMessage(message) || adaptConversationMessage(message).segments.length > 0), [state.messages]);
  const key = scrollKey(scope, activeSessionId);
  const busy = state.phase === "loading" || state.phase === "recovering";
  const waitingForAnswer = state.running || state.phase === "sending";
  const canCompose = state.phase !== "signed-out" && state.phase !== "loading"
    && state.phase !== "recovering";

  function matchesCurrentContext(): boolean {
    const current = controller.snapshot();
    return current.scope?.cacheKey === scope.cacheKey && (!sessionId || current.sessionId === sessionId);
  }

  useEffect(() => {
    controller.setOwner(scope);
  }, [controller, scope.cacheKey]);

  useEffect(() => {
    if (sessionId && controller.snapshot().sessionId !== sessionId) {
      void controller.open(sessionId);
    }
  }, [controller, scope.cacheKey, sessionId]);

  useEffect(() => {
    const restore = scrollY ?? rememberedScroll.get(key) ?? 0;
    requestAnimationFrame(() => list.current?.scrollTo({ y: restore, animated: false }));
    followLatest.current = restore === 0;
    setAwayFromLatest(restore > 0);
  }, [key, scrollY]);

  function setDraft(value: string) {
    if (!matchesCurrentContext()) return;
    controller.setDraft(value);
  }

  function send() {
    if (!matchesCurrentContext()) return;
    void controller.send();
  }

  function recover() {
    if (!matchesCurrentContext()) return;
    const current = controller.snapshot();
    if (current.sessionId) void controller.refresh();
    else void controller.send();
  }

  function handleScroll(offset: number) {
    rememberedScroll.delete(key);
    rememberedScroll.set(key, offset);
    while (rememberedScroll.size > SCROLL_MEMORY_LIMIT) {
      const first = rememberedScroll.keys().next().value;
      if (first === undefined) break;
      rememberedScroll.delete(first);
    }
    onScrollChange?.(offset);
  }

  return (
    <View style={chatStyles.root} accessibilityLabel="Gideon conversation">
      <ScrollView
        ref={list}
        nativeID="gideon-chat-scroll"
        style={chatStyles.scroll}
        contentContainerStyle={chatStyles.content}
        showsVerticalScrollIndicator={false}
        keyboardShouldPersistTaps="handled"
        scrollEventThrottle={80}
        onScroll={event => {
          const native = event.nativeEvent;
          const offset = native.contentOffset.y;
          handleScroll(offset);
          const nearEnd = native.contentSize.height - offset - native.layoutMeasurement.height < 100;
          followLatest.current = nearEnd;
          setAwayFromLatest(messages.length > 0 && !nearEnd);
        }}
        onContentSizeChange={() => {
          if (messages.length > 0 && followLatest.current) list.current?.scrollToEnd({ animated: false });
        }}
      >
        {busy && messages.length === 0 && (
          <View accessibilityLiveRegion="polite" aria-live="polite" style={[chatStyles.statusCard, { backgroundColor: palette.sky, borderColor: palette.line }]}>
            <Text style={[chatStyles.statusText, { color: palette.text }]}>
              {state.phase === "recovering" ? "Refreshing Gideon conversation…" : "Opening Gideon conversation…"}
            </Text>
          </View>
        )}

        {!busy && state.phase === "idle" && messages.length === 0 && (
          <View accessibilityLabel="Start a Gideon conversation" style={chatStyles.empty}>
            <Text style={[chatStyles.emptyTitle, { color: palette.text }]}>A little help. A lot more room for life.</Text>
            <Text style={[chatStyles.emptyDetail, { color: palette.muted }]}>
              Tell Gideon what is on your mind. Start with a question, a plan, or a decision you are working through.
            </Text>
            <View style={chatStyles.suggestions}>
              {suggestions.map(suggestion => (
                <Pressable
                  key={suggestion}
                  accessibilityRole="button"
                  disabled={contextMismatch}
                  onPress={() => {
                    if (!matchesCurrentContext()) return;
                    controller.setDraft(suggestion);
                    void controller.send();
                  }}
                  style={({ pressed }) => [chatStyles.suggestion, { backgroundColor: palette.card, borderColor: palette.line }, pressed && { opacity: 0.75 }]}
                >
                  <Text style={[chatStyles.suggestionText, { color: palette.text }]}>{suggestion}</Text>
                </Pressable>
              ))}
            </View>
          </View>
        )}

        {messages.map(message => {
          const isUser = message.role === "user";
          const adapted = adaptConversationMessage(message);
          const selected = selectedMessageId === message.id;
          return (
            <View
              key={message.id}
              nativeID={`gideon-message-${message.id}`}
              accessibilityLabel={isUser ? "Your message" : "Gideon response"}
              style={[chatStyles.message, isUser ? chatStyles.userMessage : chatStyles.assistantMessage,
                { backgroundColor: isUser ? palette.blue : palette.card, borderWidth: selected ? 2 : 0, borderColor: palette.blueDark }]}
            >
              <Text style={[chatStyles.role, { color: isUser ? palette.text : palette.blueDark }]}>
                {isUser ? "You" : "Gideon"}
              </Text>
              {isUser ? (
                <Text selectable style={[chatStyles.messageText, { color: palette.text }]}>{message.content}</Text>
              ) : (
                <AssistantResponse content={message.content} segments={adapted.segments} onNavigate={route => {
                  if (!matchesCurrentContext()) return;
                  navigate?.({ ...route, returnTo: {
                    destination: "chat", sessionId: activeSessionId ?? undefined,
                    selectionId: message.id, scrollY: rememberedScroll.get(key) ?? 0,
                  } });
                }} />
              )}
            </View>
          );
        })}

        {!state.connected && !!state.sessionId && state.phase !== "signed-out" && (
          <View accessibilityLiveRegion="polite" aria-live="polite" style={[chatStyles.statusCard, { backgroundColor: palette.orange, borderColor: palette.line }]}>
            <Text style={[chatStyles.statusText, { color: palette.text }]}>Connection interrupted. Gideon is reconnecting; submitted messages will not be sent again automatically.</Text>
          </View>
        )}

        {waitingForAnswer && (
          <View accessibilityLiveRegion="polite" aria-live="polite" accessibilityLabel="Gideon is working" style={[chatStyles.loadingDots, { backgroundColor: palette.card }]}>
            {[0.4, 0.7, 1].map(opacity => <View key={opacity} style={[chatStyles.loadingDot, { backgroundColor: palette.muted, opacity }]} />)}
            <Text accessibilityLiveRegion="polite" style={[chatStyles.statusText, { color: palette.muted }]}>Gideon is working</Text>
          </View>
        )}

        {!!state.error && (
          <View accessibilityRole="alert" style={[chatStyles.alertCard, { backgroundColor: palette.dangerSurface, borderColor: palette.line }]}>
            <Text style={[chatStyles.alertText, { color: palette.danger }]}>{state.error}</Text>
            <Pressable
              accessibilityRole="button"
              accessibilityLabel={state.phase === "uncertain" ? "Refresh before retrying" : "Recover conversation"}
              disabled={busy || contextMismatch}
              onPress={recover}
              style={({ pressed }) => [chatStyles.retry, { backgroundColor: palette.card }, pressed && { opacity: 0.75 }]}
            >
              <View style={{ flexDirection: "row", alignItems: "center", gap: 7 }}>
                <Text accessibilityElementsHidden importantForAccessibility="no-hide-descendants" style={{ color: palette.text, fontSize: 16 }}>↻</Text>
                <Text style={[chatStyles.retryText, { color: palette.text }]}>
                  {state.phase === "uncertain" ? "Refresh before retrying" : "Recover conversation"}
                </Text>
              </View>
            </Pressable>
          </View>
        )}
      </ScrollView>

      {awayFromLatest && (
        <Pressable
          accessibilityRole="button"
          disabled={contextMismatch}
          onPress={() => {
            if (!matchesCurrentContext()) return;
            followLatest.current = true;
            setAwayFromLatest(false);
            list.current?.scrollToEnd({ animated: true });
          }}
          style={[chatStyles.retry, { alignSelf: "center", backgroundColor: palette.card, marginBottom: 8 }]}
        >
          <View style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
            <Text accessibilityElementsHidden importantForAccessibility="no-hide-descendants" style={{ color: palette.text, fontSize: 16 }}>↓</Text>
            <Text style={[chatStyles.retryText, { color: palette.text }]}>Latest messages</Text>
          </View>
        </Pressable>
      )}

      <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined} style={chatStyles.footer}>
        {returnTo && onReturn && (
          <Pressable accessibilityRole="button" accessibilityLabel="Return to previous workspace" disabled={contextMismatch} onPress={() => {
            if (matchesCurrentContext()) onReturn();
          }} style={{ alignSelf: "center", padding: 10 }}>
            <Text style={[chatStyles.statusText, { color: palette.blueDark }]}>Return to previous workspace</Text>
          </Pressable>
        )}
        {state.phase === "uncertain" && (
          <Text accessibilityLiveRegion="polite" aria-live="polite" style={[chatStyles.statusText, { color: palette.muted, marginBottom: 8 }]}>Refresh the conversation before deciding whether to retry.</Text>
        )}
        <Composer
          value={state.draft}
          disabled={!canCompose || contextMismatch}
          sendDisabled={state.phase === "uncertain" || contextMismatch}
          busy={state.phase === "sending" || state.running}
          placeholder={state.phase === "uncertain" ? "Refresh before retrying…" : "Message Gideon…"}
          onChange={setDraft}
          onSend={send}
        />
      </KeyboardAvoidingView>
    </View>
  );
}

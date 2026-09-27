import { StyleSheet } from "react-native";

export const chatStyles = StyleSheet.create({
  root: { flex: 1, minWidth: 0, minHeight: 0, width: "100%" },
  scroll: { flex: 1, minWidth: 0 },
  content: { flexGrow: 1, gap: 13, paddingHorizontal: 18, paddingTop: 18, paddingBottom: 24 },
  empty: { flexGrow: 1, justifyContent: "center", alignItems: "center", paddingVertical: 36, gap: 13 },
  emptyTitle: { fontSize: 28, fontWeight: "600", letterSpacing: -0.9, textAlign: "center" },
  emptyDetail: { maxWidth: 360, fontSize: 15, lineHeight: 23, textAlign: "center" },
  suggestions: { width: "100%", maxWidth: 460, gap: 8, marginTop: 10 },
  suggestion: { minHeight: 44, justifyContent: "center", borderRadius: 22, borderWidth: 1, paddingHorizontal: 16, paddingVertical: 11 },
  suggestionText: { fontSize: 14, fontWeight: "600", textAlign: "center" },
  message: { gap: 7, maxWidth: "96%" },
  userMessage: { alignSelf: "flex-end", maxWidth: "86%", paddingHorizontal: 16, paddingVertical: 13, borderRadius: 22, borderBottomRightRadius: 7 },
  assistantMessage: { alignSelf: "flex-start", maxWidth: "96%", paddingHorizontal: 16, paddingVertical: 13, borderRadius: 22, borderBottomLeftRadius: 7 },
  role: { fontSize: 11, fontWeight: "700", letterSpacing: 0.6, textTransform: "uppercase" },
  messageText: { fontSize: 16, lineHeight: 24 },
  statusCard: { alignSelf: "flex-start", maxWidth: "100%", paddingHorizontal: 14, paddingVertical: 10, borderRadius: 16, borderWidth: 1 },
  statusText: { fontSize: 13, lineHeight: 19 },
  alertCard: { alignSelf: "stretch", paddingHorizontal: 14, paddingVertical: 12, borderRadius: 15, borderWidth: 1, gap: 8 },
  alertText: { fontSize: 14, lineHeight: 21 },
  retry: { alignSelf: "flex-start", minHeight: 40, justifyContent: "center", borderRadius: 20, paddingHorizontal: 14 },
  retryText: { fontSize: 13, fontWeight: "600" },
  loadingDots: { alignSelf: "flex-start", minHeight: 44, flexDirection: "row", alignItems: "center", gap: 7, paddingHorizontal: 17, borderRadius: 24 },
  loadingDot: { width: 7, height: 7, borderRadius: 4 },
  footer: { paddingHorizontal: 18, paddingTop: 10, paddingBottom: 16 },
});

export const assistantResponseStyles = StyleSheet.create({
  container: { minWidth: 0 },
  text: { fontSize: 16, lineHeight: 24, flexShrink: 1 },
});

export const composerStyles = StyleSheet.create({
  shell: {
    width: "100%", maxWidth: 760, flexDirection: "row", alignItems: "flex-end", gap: 8,
    borderWidth: 1, borderRadius: 28, padding: 8,
    shadowColor: "#18384B", shadowOpacity: 0.06, shadowRadius: 18,
    shadowOffset: { width: 0, height: 4 }, elevation: 3,
  },
  focused: { shadowOpacity: 0.11 },
  input: { flex: 1, minWidth: 0, minHeight: 44, maxHeight: 144, paddingHorizontal: 10, paddingVertical: 11, fontSize: 16, lineHeight: 23 },
  send: { minWidth: 76, minHeight: 44, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 5, paddingHorizontal: 13, borderRadius: 22 },
  sendDisabled: { opacity: 0.48 },
  sendPressed: { transform: [{ scale: 0.97 }] },
  sendText: { fontSize: 13, fontWeight: "700" },
});

import * as React from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";
import type { BootstrapState } from "../bootstrap.web";

const s = StyleSheet.create({
  heading: { color: "#11191C", fontSize: 16, fontWeight: "600", letterSpacing: -0.25 },
  small: { color: "#697176", fontSize: 11, lineHeight: 17 },
  row: { flexDirection: "row", alignItems: "center" },
});

export type ShellIdentityView = Readonly<{
  name: string;
  status: string;
  available: boolean;
}>;

export function shellIdentityView(state: BootstrapState): ShellIdentityView {
  if (state.phase === "ready") {
    return { name: "Gideon", status: `Signed in as ${state.owner.user}`, available: true };
  }
  if (state.phase === "checking" || state.phase === "signing_in" || state.phase === "signing_out") {
    return { name: "Gideon", status: "Checking session…", available: false };
  }
  return { name: "Gideon", status: state.phase === "unavailable" ? "Connection unavailable" : "Sign in required", available: false };
}

export const CONSOLE_HANDOFFS = {
  chat: { label: "Chat", href: "/#/chat/new" },
  activity: { label: "Tasks", href: "/#/tasks" },
  ideas: { label: "Ideas", href: "/#/capabilities/knowledge/ideas" },
  goals: { label: "Goals", href: "/#/capabilities/identity/goals" },
  apps: { label: "Apps", href: "/#/apps" },
  inbox: { label: "Notifications", href: "/#/inbox" },
} as const;

export function ShellIdentity({ state, onRefresh, onSignOut }: {
  state: BootstrapState;
  onRefresh: () => void;
  onSignOut: () => void;
}) {
  const identity = shellIdentityView(state);
  return (
    <View style={{ alignItems: "center", gap: 3 }}>
      <Text style={s.heading}>{identity.name}</Text>
      <Text style={s.small}>{identity.status}</Text>
      {identity.available && <View style={[s.row, { gap: 14, marginTop: 4 }]}>
        <Pressable accessibilityRole="button" accessibilityLabel="Refresh Gideon session" onPress={onRefresh}>
          <Text style={[s.small, { color: "#1473C8" }]}>Refresh</Text>
        </Pressable>
        <Pressable accessibilityRole="button" accessibilityLabel="Sign out of Gideon" onPress={onSignOut}>
          <Text style={[s.small, { color: "#1473C8" }]}>Sign out</Text>
        </Pressable>
      </View>}
    </View>
  );
}

import { StatusBar } from "expo-status-bar";
import { Bell, Lightbulb, Menu, MessageCircle, PanelsTopLeft, Shapes, SquareCheck } from "lucide-react-native";
import { useState } from "react";
import { Platform, Text, useWindowDimensions, View } from "react-native";
import { SafeAreaProvider, SafeAreaView } from "react-native-safe-area-context";
import { AssistantBootstrapProvider, useAssistantBootstrap, type BootstrapState } from "./src/shared/bootstrap.web";
import { targetSessionId, useAssistantEntry } from "./src/features/delivery/entry.web";
import { CONSOLE_HANDOFFS, ShellIdentity } from "./src/shared/shell/ShellIdentity";
import { ShellNavigation, type ShellNavigationIcon } from "./src/shared/shell/ShellNavigation";
import { ShellThemeControls, ShellThemeProvider, useShellTheme } from "./src/shared/shell/shellTheme";
import { createShellRoute, SHELL_DESTINATIONS, type ShellDestination } from "./src/shared/shell/shellRoutes";
import { WorkspaceFrame } from "./src/shared/shell/WorkspaceFrame";
import { moduleForRoute, TrustedModuleContent } from "./src/shared/shell/webModules";
import { Avatar, Button, Card, IconButton, LinkRow, s, Sheet } from "./src/ui";

const icons: Record<ShellDestination, ShellNavigationIcon> = {
  chat: MessageCircle,
  activity: PanelsTopLeft,
  ideas: Lightbulb,
  goals: SquareCheck,
  apps: Shapes,
};

const titles: Record<ShellDestination, { title: string; subtitle: string; console: keyof typeof CONSOLE_HANDOFFS }> = {
  chat: { title: "Chat", subtitle: "Continue your conversation in Gideon console.", console: "chat" },
  activity: { title: "Activity", subtitle: "Review tasks in Gideon console.", console: "activity" },
  ideas: { title: "Ideas", subtitle: "Open your ideas in Gideon console.", console: "ideas" },
  goals: { title: "Goals", subtitle: "Open your goals in Gideon console.", console: "goals" },
  apps: { title: "Apps", subtitle: "Open your apps in Gideon console.", console: "apps" },
};

function openConsole(key: keyof typeof CONSOLE_HANDOFFS) {
  if (Platform.OS === "web") window.location.assign(CONSOLE_HANDOFFS[key].href);
}

export default function App() {
  return (
    <SafeAreaProvider>
      <ShellThemeProvider>
        <ThemedStatusBar />
        {Platform.OS === "web" ? (
          <AssistantBootstrapProvider><WorkspaceApp /></AssistantBootstrapProvider>
        ) : <NativeNotice />}
      </ShellThemeProvider>
    </SafeAreaProvider>
  );
}

function ThemedStatusBar() {
  const { mode } = useShellTheme();
  return <StatusBar style={mode === "dark" ? "light" : "dark"} />;
}

function NativeNotice() {
  const { palette } = useShellTheme();
  return <SafeAreaView style={{ flex: 1, backgroundColor: palette.canvas, justifyContent: "center", alignItems: "center", padding: 24 }}>
    <Avatar size={72} />
    <Text style={[s.title, { color: palette.text, textAlign: "center", marginTop: 16 }]}>Gideon</Text>
    <Text style={[s.muted, { color: palette.muted, textAlign: "center", marginTop: 12 }]}>Open Gideon in your authenticated browser.</Text>
  </SafeAreaView>;
}

function WorkspaceApp() {
  const { state, refresh, signOut } = useAssistantBootstrap();
  if (state.phase !== "ready") return null;
  return <ReadyWorkspace state={state} refresh={refresh} signOut={signOut} />;
}

function ReadyWorkspace({ state, refresh, signOut }: {
  state: Extract<BootstrapState, { phase: "ready" }>;
  refresh: () => Promise<void>;
  signOut: () => Promise<void>;
}) {
  const { palette } = useShellTheme();
  const { snapshot, navigate, refresh: refreshRoute, session } = useAssistantEntry(state.scope);
  const section: ShellDestination = snapshot.route.kind === "route" ? snapshot.route.destination : "chat";
  const [menuOpen, setMenuOpen] = useState(false);
  const { width, fontScale } = useWindowDimensions();
  const desktop = width >= 900;
  const title = titles[section];
  const routeForLinks = snapshot.route.kind === "route" ? snapshot.route : snapshot.route.action.route;
  const returnTo = snapshot.route.kind === "route" ? snapshot.route.returnTo : undefined;
  const trustedModule = snapshot.phase === "ready" && snapshot.route.kind === "route"
    ? moduleForRoute(snapshot.route) : undefined;
  const moduleRoute = snapshot.phase === "ready" && snapshot.route.kind === "route" ? snapshot.route : undefined;
  const workspaceTitle = trustedModule?.id === "tasks" ? "Task details"
    : trustedModule?.id === "artifacts/editor" ? "Artifact editor" : title.title;
  const returnToAssistant = () => {
    if (!returnTo) return;
    navigate(createShellRoute(returnTo.destination, {
      view: returnTo.placement ? "workspace" : returnTo.record ? "detail" : returnTo.sessionId ? "workspace" : "list",
      record: returnTo.record,
      placement: returnTo.placement,
      sessionId: returnTo.sessionId,
    }));
  };
  const onModuleReturn = () => {
    if (returnTo) returnToAssistant();
    else navigate(createShellRoute(section));
  };
  const frameState = snapshot.phase === "checking" ? { kind: "loading" as const, message: "Opening Gideon destination…" }
    : snapshot.phase === "unavailable" ? { kind: "error" as const, message: snapshot.message, onRetry: refreshRoute }
      : { kind: "ready" as const };

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: palette.canvas }} edges={["top", "bottom"]}>
      <View style={{ flex: 1, minHeight: 0, width: "100%", alignSelf: "stretch" }}>
        <View style={{ height: desktop ? 154 : 132, paddingTop: desktop ? 14 : 2, marginHorizontal: 20 }}>
          <View style={{ position: "absolute", left: 0, top: 16 }}>
            <IconButton icon={Menu} label="Open Gideon menu" onPress={() => setMenuOpen(true)} />
          </View>
          <View style={{ alignItems: "center", gap: 1 }}>
            <Avatar size={desktop ? 58 : 49} />
            <ShellIdentity state={state} onRefresh={() => void refresh()} onSignOut={() => void signOut()} />
          </View>
          <View style={{ position: "absolute", right: 0, top: 16 }}>
            <IconButton icon={Bell} label="Open notifications in Gideon console" onPress={() => openConsole("inbox")} />
          </View>
        </View>

        <View style={{ flex: 1, minHeight: 0 }}>
          {trustedModule && moduleRoute ? <TrustedModuleContent definition={trustedModule}
              route={moduleRoute} scope={state.scope} navigate={navigate} returnTo={returnTo} onReturn={onModuleReturn} />
            : <WorkspaceFrame route={snapshot.route} mode={section === "chat" ? "compact" : "full"}
              title={workspaceTitle} state={frameState}
              onGoToChat={() => navigate(snapshot.route.kind === "recovery" ? snapshot.route.action.route : createShellRoute("chat"))}
              actions={returnTo && <Button onPress={returnToAssistant}>
                Return to previous workspace
              </Button>}>
              {section !== "chat" ? <Card>
              <Text style={[s.heading, { color: palette.text }]}>{title.title}</Text>
              <Text style={[s.muted, { color: palette.muted, marginTop: 8, marginBottom: 18 }]}>{title.subtitle}</Text>
              <Button primary onPress={() => openConsole(title.console)}>
                Open {CONSOLE_HANDOFFS[title.console].label} in Gideon console
              </Button>
            </Card> : <View style={{ flex: 1,
            paddingHorizontal: desktop ? 42 : 17, justifyContent: "flex-end", paddingBottom: 22 }}>
            <View style={{ flex: 1, justifyContent: "center", alignItems: "center", gap: 13 }}>
              <Avatar size={68} variant="lilac" />
              <Text style={[s.title, { color: palette.text, textAlign: "center" }]}>
                {session ? session.title || "Gideon conversation" : "Your Gideon workspace"}
              </Text>
              <Text style={[s.muted, { color: palette.muted, textAlign: "center", maxWidth: 360 }]}>
                {session ? `${session.total} messages in this conversation.` : "Your conversation is available in Gideon console."}
              </Text>
            </View>
            <Card style={{ gap: 12, borderWidth: 1 }}>
              <Text style={[s.muted, { color: palette.muted }]}>Continue in your authenticated conversation</Text>
              <Button primary onPress={() => window.location.assign(session
                ? `/#/chat/${encodeURIComponent(targetSessionId(routeForLinks) ?? "")}` : CONSOLE_HANDOFFS.chat.href)}>
                Open Chat in Gideon console
              </Button>
            </Card>
          </View>}
            </WorkspaceFrame>}
        </View>

        {snapshot.phase !== "recovery" && snapshot.phase !== "checking" && <View style={{ paddingHorizontal: 22, paddingTop: 10, paddingBottom: desktop ? 22 : 7, alignItems: "center" }}>
          <ShellNavigation selected={section} onSelect={(next) => navigate(createShellRoute(next))}
            availableWidth={Math.max(1, Math.min(width - 44, 540))} fontScale={fontScale} icons={icons} />
        </View>}
      </View>

      {menuOpen && <Sheet title="Gideon" subtitle={`Signed in as ${state.owner.user}`} onClose={() => setMenuOpen(false)}>
        <Text style={[s.label, { color: palette.muted, marginBottom: 12 }]}>Your workspace</Text>
        {SHELL_DESTINATIONS.map((item) => (
          <LinkRow key={item.id} icon={icons[item.id]} title={item.label}
            detail={`View ${item.label} in this assistant`} onPress={() => { navigate(createShellRoute(item.id)); setMenuOpen(false); }} />
        ))}
        <View style={[s.divider, { backgroundColor: palette.line }]} />
        <Text style={[s.label, { color: palette.muted, marginBottom: 12 }]}>Appearance</Text>
        <ShellThemeControls />
        <View style={[s.divider, { backgroundColor: palette.line }]} />
        <Button onPress={() => { setMenuOpen(false); void refresh(); }}>Refresh session</Button>
        <View style={{ height: 10 }} />
        <Button onPress={() => { setMenuOpen(false); void signOut(); }}>Sign out</Button>
      </Sheet>}
    </SafeAreaView>
  );
}

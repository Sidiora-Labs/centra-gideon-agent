import { StatusBar } from "expo-status-bar";
import { Bell, Lightbulb, Menu, MessageCircle, PanelsTopLeft, Shapes, SquareCheck } from "lucide-react-native";
import { useState } from "react";
import { ActivityIndicator, Platform, ScrollView, Text, useWindowDimensions, View } from "react-native";
import { SafeAreaProvider, SafeAreaView } from "react-native-safe-area-context";
import { AssistantBootstrapProvider, useAssistantBootstrap } from "./src/shared/bootstrap.web";
import { CONSOLE_HANDOFFS, ShellIdentity } from "./src/shared/shell/ShellIdentity";
import { ShellNavigation, type ShellNavigationIcon } from "./src/shared/shell/ShellNavigation";
import { SHELL_DESTINATIONS, type ShellDestination } from "./src/shared/shell/shellRoutes";
import { Avatar, Button, Card, colors, IconButton, LinkRow, s, Sheet } from "./src/ui";

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
      <StatusBar style="dark" />
      {Platform.OS === "web" ? (
        <AssistantBootstrapProvider><WorkspaceApp /></AssistantBootstrapProvider>
      ) : (
        <SafeAreaView style={{ flex: 1, backgroundColor: colors.canvas, justifyContent: "center", alignItems: "center", padding: 24 }}>
          <Avatar size={72} />
          <Text style={[s.title, { textAlign: "center", marginTop: 16 }]}>Gideon</Text>
          <Text style={[s.muted, { textAlign: "center", marginTop: 12 }]}>Open Gideon in your authenticated browser.</Text>
        </SafeAreaView>
      )}
    </SafeAreaProvider>
  );
}

function WorkspaceApp() {
  const { state, refresh, signOut } = useAssistantBootstrap();
  const [section, setSection] = useState<ShellDestination>("chat");
  const [menuOpen, setMenuOpen] = useState(false);
  const { width, fontScale } = useWindowDimensions();
  const desktop = width >= 900;
  const title = titles[section];

  if (state.phase !== "ready") return (
    <SafeAreaView style={{ flex: 1, backgroundColor: colors.canvas, alignItems: "center", justifyContent: "center" }}>
      <ActivityIndicator color={colors.blueDark} />
      <Text style={[s.muted, { marginTop: 12 }]}>Checking Gideon session…</Text>
    </SafeAreaView>
  );

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: colors.canvas }} edges={["top", "bottom"]}>
      <View style={{ flex: 1, width: "100%", maxWidth: 760, alignSelf: "center" }}>
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
          {section !== "chat" && (
            <ScrollView key={section} showsVerticalScrollIndicator={false}
              contentContainerStyle={{ paddingHorizontal: desktop ? 42 : 22, paddingBottom: 28 }}
              keyboardShouldPersistTaps="handled">
              <Text style={[s.title, { fontSize: 25, marginBottom: 22 }]}>{title.title}</Text>
              <Card>
                <Text style={s.heading}>{title.title}</Text>
                <Text style={[s.muted, { marginTop: 8, marginBottom: 18 }]}>{title.subtitle}</Text>
                <Button primary onPress={() => openConsole(title.console)}>
                  Open {CONSOLE_HANDOFFS[title.console].label} in Gideon console
                </Button>
              </Card>
            </ScrollView>
          )}
          <View style={{ display: section === "chat" ? "flex" : "none", flex: 1,
            paddingHorizontal: desktop ? 42 : 17, justifyContent: "flex-end", paddingBottom: 22 }}>
            <View style={{ flex: 1, justifyContent: "center", alignItems: "center", gap: 13 }}>
              <Avatar size={68} variant="lilac" />
              <Text style={[s.title, { textAlign: "center" }]}>Your Gideon workspace</Text>
              <Text style={[s.muted, { textAlign: "center", maxWidth: 360 }]}>
                Your conversation is available in Gideon console.
              </Text>
            </View>
            <Card style={{ gap: 12, borderWidth: 1 }}>
              <Text style={s.muted}>Continue in your authenticated conversation</Text>
              <Button primary onPress={() => openConsole("chat")}>Open Chat in Gideon console</Button>
            </Card>
          </View>
        </View>

        <View style={{ paddingHorizontal: 22, paddingTop: 10, paddingBottom: desktop ? 22 : 7, alignItems: "center" }}>
          <ShellNavigation selected={section} onSelect={setSection}
            availableWidth={Math.max(1, Math.min(width - 44, 540))} fontScale={fontScale} icons={icons} />
        </View>
      </View>

      {menuOpen && <Sheet title="Gideon" subtitle={`Signed in as ${state.owner.user}`} onClose={() => setMenuOpen(false)}>
        <Text style={[s.label, { marginBottom: 12 }]}>Your workspace</Text>
        {SHELL_DESTINATIONS.map((item) => (
          <LinkRow key={item.id} icon={icons[item.id]} title={item.label}
            detail={`View ${item.label} in this assistant`} onPress={() => { setSection(item.id); setMenuOpen(false); }} />
        ))}
        <View style={s.divider} />
        <Button onPress={() => { setMenuOpen(false); void refresh(); }}>Refresh session</Button>
        <View style={{ height: 10 }} />
        <Button onPress={() => { setMenuOpen(false); void signOut(); }}>Sign out</Button>
      </Sheet>}
    </SafeAreaView>
  );
}

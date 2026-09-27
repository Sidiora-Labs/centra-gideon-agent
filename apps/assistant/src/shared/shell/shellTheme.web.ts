import * as React from "react";
import { Pressable, Text, View } from "react-native";
import "./shellTheme.web.css";

export type ShellThemePreference = "light" | "dark" | "system";
export type ShellThemeMode = "light" | "dark";

export const shellPalettes = {
  light: {
    canvas: "#FCFCFC", card: "#FFFFFF", text: "#11191C", muted: "#697176",
    line: "#E4E7E9", blue: "#C8E7FF", blueDark: "#1473C8", sky: "#EDF7FD",
    green: "#E3F3E8", lavender: "#F0EEFA", orange: "#FDF0DF",
    danger: "#A3322C", dangerSurface: "#FBEFED", secondary: "#F1F2F3",
    shade: "rgba(35,48,44,0.40)",
  },
  dark: {
    canvas: "#11191E", card: "#1D292F", text: "#F5FAFC", muted: "#BDC9CE",
    line: "#405057", blue: "#245D81", blueDark: "#9BD5FC", sky: "#233D4D",
    green: "#244B3D", lavender: "#3A3555", orange: "#4D3A27",
    danger: "#FFB9B4", dangerSurface: "#4B2A2B", secondary: "#2B3940",
    shade: "rgba(0,0,0,0.70)",
  },
} as const;

export type ShellPalette = { [K in keyof typeof shellPalettes.light]: string };
export function resolveShellTheme(preference: ShellThemePreference, system: ShellThemeMode): ShellThemeMode {
  return preference === "system" ? system : preference;
}
type ShellThemeContextValue = {
  preference: ShellThemePreference;
  mode: ShellThemeMode;
  palette: ShellPalette;
  direction: "ltr" | "rtl";
  setPreference: (value: ShellThemePreference) => void;
};

const storageKey = "gideon-assistant-theme";
const defaultTheme: ShellThemeContextValue = {
  preference: "system", mode: "light", palette: shellPalettes.light, direction: "ltr",
  setPreference: () => {},
};
const ShellThemeContext = React.createContext<ShellThemeContextValue>(defaultTheme);

function storedPreference(): ShellThemePreference {
  if (typeof window === "undefined") return "system";
  try {
    const value = window.localStorage.getItem(storageKey);
    return value === "light" || value === "dark" ? value : "system";
  } catch {
    return "system";
  }
}

function systemMode(): ShellThemeMode {
  return typeof window !== "undefined" && window.matchMedia?.("(prefers-color-scheme: dark)").matches
    ? "dark" : "light";
}

function documentDirection(): "ltr" | "rtl" {
  return typeof document !== "undefined" && document.documentElement.dir.toLowerCase() === "rtl" ? "rtl" : "ltr";
}

export function ShellThemeProvider({ children, initialPreference }: {
  children: React.ReactNode; initialPreference?: ShellThemePreference;
}) {
  const [preference, setPreference] = React.useState<ShellThemePreference>(() => initialPreference ?? storedPreference());
  const [system, setSystem] = React.useState<ShellThemeMode>(systemMode);
  const [direction, setDirection] = React.useState<"ltr" | "rtl">(documentDirection);

  React.useEffect(() => {
    const query = window.matchMedia?.("(prefers-color-scheme: dark)");
    if (!query) return;
    const update = () => setSystem(query.matches ? "dark" : "light");
    update();
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);

  React.useEffect(() => {
    if (typeof document === "undefined") return;
    if (typeof MutationObserver === "undefined") return;
    const observer = new MutationObserver(() => setDirection(documentDirection()));
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["dir"] });
    return () => observer.disconnect();
  }, []);

  const updatePreference = React.useCallback((value: ShellThemePreference) => {
    setPreference(value);
    try { window.localStorage.setItem(storageKey, value); } catch {}
  }, []);
  const mode = resolveShellTheme(preference, system);
  const value = React.useMemo<ShellThemeContextValue>(() => ({
    preference, mode, palette: shellPalettes[mode], direction, setPreference: updatePreference,
  }), [preference, mode, direction, updatePreference]);

  const ScopedView = View as React.ComponentType<React.ComponentProps<typeof View> & { dataSet?: Record<string, string> }>;
  return React.createElement(ShellThemeContext.Provider, { value },
    React.createElement(ScopedView, {
      dataSet: { gideonAssistant: "", theme: mode, direction },
      style: { flex: 1, backgroundColor: value.palette.canvas },
    }, children));
}

export function useShellTheme() { return React.useContext(ShellThemeContext); }

export function ShellThemeControls() {
  const { preference, setPreference, palette } = useShellTheme();
  return React.createElement(View, { accessibilityLabel: "Appearance", style: { flexDirection: "row", flexWrap: "wrap", gap: 8 } },
    (["light", "dark", "system"] as const).map((choice) => React.createElement(Pressable, {
      key: choice,
      accessibilityRole: "radio",
      accessibilityLabel: `${choice[0].toUpperCase()}${choice.slice(1)} theme`,
      accessibilityState: { checked: preference === choice },
      "aria-checked": preference === choice,
      onPress: () => setPreference(choice),
      style: { minHeight: 44, minWidth: 70, paddingHorizontal: 14, borderRadius: 22, justifyContent: "center",
        alignItems: "center", backgroundColor: preference === choice ? palette.blue : palette.secondary },
    }, React.createElement(Text, { style: { color: palette.text, fontSize: 14, fontWeight: "600" } },
      choice[0].toUpperCase() + choice.slice(1)))));
}

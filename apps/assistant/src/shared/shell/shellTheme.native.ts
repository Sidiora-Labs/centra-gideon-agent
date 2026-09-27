import * as React from "react";
import { Pressable, Text, useColorScheme, View } from "react-native";

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
type ShellThemeContextValue = {
  preference: ShellThemePreference;
  mode: ShellThemeMode;
  palette: ShellPalette;
  direction: "ltr" | "rtl";
  setPreference: (value: ShellThemePreference) => void;
};
const ShellThemeContext = React.createContext<ShellThemeContextValue>({
  preference: "system", mode: "light", palette: shellPalettes.light,
  direction: "ltr", setPreference: () => {},
});

export function ShellThemeProvider({ children, initialPreference = "system" }: {
  children: React.ReactNode; initialPreference?: ShellThemePreference;
}) {
  const system = useColorScheme();
  const [preference, setPreference] = React.useState<ShellThemePreference>(initialPreference);
  const mode = preference === "system" ? (system === "dark" ? "dark" : "light") : preference;
  const value = React.useMemo<ShellThemeContextValue>(() => ({
    preference, mode, palette: shellPalettes[mode], direction: "ltr", setPreference,
  }), [preference, mode]);
  return React.createElement(ShellThemeContext.Provider, { value },
    React.createElement(View, { style: { flex: 1, backgroundColor: value.palette.canvas } }, children));
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

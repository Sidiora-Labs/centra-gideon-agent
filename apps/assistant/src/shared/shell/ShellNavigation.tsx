import * as React from "react";
import type { ComponentType } from "react";
import { Pressable, ScrollView, Text, View } from "react-native";
import { SHELL_DESTINATIONS, type ShellDestination } from "./shellRoutes";

export type ShellNavigationIcon = ComponentType<{ size?: number; color?: string; strokeWidth?: number }>;
const colors = { card: "#FFFFFF", canvas: "#FCFCFC", line: "#EEEEF0", text: "#11191C", blueDark: "#1473C8" };

export type ShellNavigationLabels = Partial<Record<ShellDestination, string>>;

export function shellNavigationColumns(width: number, fontScale: number, labels: ShellNavigationLabels = {}): number {
  const longest = Math.max(...SHELL_DESTINATIONS.map(({ id, label }) => (labels[id] || label).length));
  const labelWidth = Math.max(64, Math.ceil(longest * 7.5 * Math.max(fontScale, 1) + 24));
  const fitting = Math.max(1, Math.min(5, Math.floor(Math.max(width, 1) / labelWidth)));
  return fitting === 5 ? 5 : Math.min(3, fitting);
}

export function ShellNavigation({ selected, onSelect, availableWidth, fontScale, labels = {}, icons }: {
  selected: ShellDestination;
  onSelect: (destination: ShellDestination) => void;
  availableWidth: number;
  fontScale: number;
  labels?: ShellNavigationLabels;
  icons?: Record<ShellDestination, ShellNavigationIcon>;
}) {
  const columns = shellNavigationColumns(availableWidth, fontScale, labels);
  return (
    <View accessibilityRole="tablist" accessibilityLabel="Assistant destinations"
      style={{ width: "100%", maxWidth: 540, backgroundColor: colors.card, borderRadius: 24,
        shadowColor: "#132631", shadowOffset: { width: 0, height: 2 }, shadowOpacity: 0.07,
        shadowRadius: 18, elevation: 3, borderWidth: 1, borderColor: colors.line }}>
      <ScrollView showsVerticalScrollIndicator={columns < 3} keyboardShouldPersistTaps="handled"
        style={{ maxHeight: 222 }}
        contentContainerStyle={{ flexDirection: "row", flexWrap: "wrap", padding: 5 }}>
        {SHELL_DESTINATIONS.map(({ id, label }) => {
          const Icon = icons?.[id];
          const active = selected === id;
          return (
            <Pressable key={id} accessibilityRole="tab" accessibilityLabel={labels[id] || label}
              accessibilityState={{ selected: active }} aria-selected={active} onPress={() => onSelect(id)}
              style={({ pressed }) => ({ width: `${100 / columns}%`, minHeight: 62, paddingHorizontal: 4,
                paddingVertical: 7, alignItems: "center", justifyContent: "center", gap: 3,
                borderRadius: 18, backgroundColor: active ? "#F0F1F2" : pressed ? colors.canvas : "transparent" })}>
              {Icon && <Icon size={20} strokeWidth={1.8} color={active ? colors.blueDark : colors.text} />}
              <Text style={{ color: active ? colors.blueDark : colors.text, fontSize: 12,
                lineHeight: 17, fontWeight: active ? "700" : "500", textAlign: "center", flexShrink: 1 }}>
                {labels[id] || label}
              </Text>
            </Pressable>
          );
        })}
      </ScrollView>
    </View>
  );
}

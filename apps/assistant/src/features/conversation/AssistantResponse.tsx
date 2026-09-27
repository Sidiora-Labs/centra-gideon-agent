import { Text, View } from "react-native";
import { useShellTheme } from "../../shared/shell/shellTheme";
import { assistantResponseStyles } from "./chatStyles";

export function AssistantResponse({ content }: { content: string }) {
  const { palette } = useShellTheme();
  return (
    <View accessibilityLabel="Gideon response" style={assistantResponseStyles.container}>
      <Text selectable style={[assistantResponseStyles.text, { color: palette.text }]}>
        {content}
      </Text>
    </View>
  );
}

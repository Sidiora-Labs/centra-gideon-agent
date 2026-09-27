import { useState } from "react";
import { Platform, Pressable, Text, TextInput, View } from "react-native";
import { useShellTheme } from "../../shared/shell/shellTheme";
import { composerStyles } from "./chatStyles";

export type ComposerProps = {
  value: string;
  disabled?: boolean;
  sendDisabled?: boolean;
  busy?: boolean;
  placeholder?: string;
  onChange: (value: string) => void;
  onSend: () => void;
};

export function Composer({ value, disabled = false, sendDisabled = false, busy = false, placeholder = "Message Gideon…", onChange, onSend }: ComposerProps) {
  const { palette } = useShellTheme();
  const [focused, setFocused] = useState(false);
  const canSend = value.trim().length > 0 && !disabled && !sendDisabled && !busy;

  function handleKeyPress(event: { nativeEvent: { key: string; shiftKey?: boolean }; preventDefault?: () => void }) {
    if (Platform.OS === "web" && event.nativeEvent.key === "Enter" && !event.nativeEvent.shiftKey && canSend) {
      event.preventDefault?.();
      onSend();
    }
  }

  return (
    <View
      style={[
        composerStyles.shell,
        { backgroundColor: palette.card, borderColor: focused ? palette.blueDark : palette.line },
        focused && composerStyles.focused,
      ]}
    >
      <TextInput
        nativeID="gideon-message-composer"
        accessibilityLabel="Message Gideon"
        accessibilityHint="Press Enter to send. Press Shift and Enter for a new line."
        aria-label="Message Gideon"
        value={value}
        onChangeText={onChange}
        onKeyPress={handleKeyPress}
        onFocus={() => setFocused(true)}
        onBlur={() => setFocused(false)}
        placeholder={placeholder}
        placeholderTextColor={palette.muted}
        selectionColor={palette.blueDark}
        editable={!disabled}
        multiline
        blurOnSubmit={false}
        returnKeyType="send"
        style={[composerStyles.input, { color: palette.text }]}
        submitBehavior="newline"
      />
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={busy ? "Sending message" : "Send message"}
        accessibilityState={{ disabled: !canSend, busy }}
        aria-disabled={!canSend}
        disabled={!canSend}
        onPress={onSend}
        hitSlop={8}
        style={({ pressed }) => [
          composerStyles.send,
          { backgroundColor: palette.blue },
          !canSend && composerStyles.sendDisabled,
          pressed && canSend && composerStyles.sendPressed,
        ]}
      >
        <Text accessibilityElementsHidden importantForAccessibility="no-hide-descendants" style={{ color: palette.text, fontSize: 18, lineHeight: 20 }}>↑</Text>
        <Text style={[composerStyles.sendText, { color: palette.text }]}>Send</Text>
      </Pressable>
    </View>
  );
}

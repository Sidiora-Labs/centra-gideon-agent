import * as React from "react";
import { Modal, View } from "react-native";

export function ShellOverlayRoot({ children, onClose }: { children: React.ReactNode; onClose: () => void }) {
  return <Modal transparent visible onRequestClose={onClose}><View style={{ flex: 1 }}>{children}</View></Modal>;
}

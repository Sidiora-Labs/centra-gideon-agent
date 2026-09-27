import { defineConfig } from "vitest/config";

export default defineConfig({
  resolve: {
    alias: [
      { find: /^react-native$/, replacement: "react-native-web" },
      { find: /^react-native-svg$/, replacement: "react-native-svg/lib/module/ReactNativeSVG.web.js" },
    ],
    extensions: [".web.mjs", ".mjs", ".web.js", ".js", ".web.tsx", ".tsx", ".web.ts", ".ts", ".json"],
  },
  test: {
    environment: "node",
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
    server: {
      deps: {
        inline: ["lucide-react-native", "react-native-svg", "react-native-safe-area-context"],
      },
    },
  },
});

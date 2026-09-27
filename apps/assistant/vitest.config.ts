import { defineConfig } from "vitest/config";
import { resolve } from "node:path";

const reactRuntime = resolve(process.cwd(), "node_modules/react");
const reactDomRuntime = resolve(process.cwd(), "node_modules/react-dom");
const expoStatusBarWeb = resolve(process.cwd(), "node_modules/expo-status-bar/src/StatusBar.web.ts");

export default defineConfig({
  resolve: {
    alias: [
      { find: /^react\/jsx-dev-runtime$/, replacement: `${reactRuntime}/jsx-dev-runtime.js` },
      { find: /^react\/jsx-runtime$/, replacement: `${reactRuntime}/jsx-runtime.js` },
      { find: /^react$/, replacement: `${reactRuntime}/index.js` },
      { find: /^react-dom\/client$/, replacement: `${reactDomRuntime}/client.js` },
      { find: /^react-dom$/, replacement: `${reactDomRuntime}/index.js` },
      { find: /^expo-status-bar$/, replacement: expoStatusBarWeb },
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

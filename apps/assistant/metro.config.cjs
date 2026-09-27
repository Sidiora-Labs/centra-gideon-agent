const path = require("node:path");
const { getDefaultConfig } = require("expo/metro-config");

const config = getDefaultConfig(__dirname);
const assistantNodeModules = path.resolve(__dirname, "node_modules");
const consoleNodeModules = path.resolve(__dirname, "../console/node_modules");
const rootNodeModules = path.resolve(__dirname, "../../node_modules");
const singletonPackages = new Set(["react", "react-dom", "react-native", "react-native-web"]);
const originalResolveRequest = config.resolver.resolveRequest;

config.watchFolders = [path.resolve(__dirname, "../console/src")];
config.resolver.nodeModulesPaths = [assistantNodeModules, consoleNodeModules, rootNodeModules];
config.resolver.extraNodeModules = {
  ...config.resolver.extraNodeModules,
  react: path.join(assistantNodeModules, "react"),
  "react-dom": path.join(assistantNodeModules, "react-dom"),
  "react-native": path.join(assistantNodeModules, "react-native"),
  "react-native-web": path.join(assistantNodeModules, "react-native-web"),
};
config.resolver.resolveRequest = (context, moduleName, platform) => {
  const packageName = moduleName.startsWith("react-native/") ? moduleName.slice(0, "react-native".length)
    : moduleName.startsWith("react-native-web/") ? moduleName.slice(0, "react-native-web".length)
      : moduleName.startsWith("react-dom/") ? "react-dom"
        : moduleName.startsWith("react/") ? "react"
          : moduleName;

  if (singletonPackages.has(packageName)) {
    const resolvedPackage = packageName === "react-native" && platform === "web" ? "react-native-web" : packageName;
    const suffix = moduleName === packageName ? "" : moduleName.slice(packageName.length + 1);
    const absoluteModule = path.join(assistantNodeModules, resolvedPackage, suffix);
    return context.resolveRequest(context, absoluteModule, platform);
  }

  return originalResolveRequest
    ? originalResolveRequest(context, moduleName, platform)
    : context.resolveRequest(context, moduleName, platform);
};

module.exports = config;

"use strict";

function buildMenuTemplate(params, platform, webContents) {
  const sections = [];
  if (params.misspelledWord) {
    const corrections = (params.dictionarySuggestions || []).slice(0, 5)
      .map((label) => ({ label, click: () => webContents.replaceMisspelling(label) }));
    sections.push(corrections.length ? corrections : [{ label: "No suggestions", enabled: false }]);
    sections.push([{ label: "Add to Dictionary", click() {
      if (!webContents.session.addWordToSpellCheckerDictionary(params.misspelledWord)) {
        console.warn(`Failed to add "${params.misspelledWord}" to dictionary`);
      }
    } }]);
  }
  if (params.isEditable) {
    const flags = params.editFlags || {};
    sections.push(["cut", "copy", "paste"].map((role) => ({ role,
      enabled: Boolean(flags["can" + role[0].toUpperCase() + role.slice(1)]) })));
    sections.push([{ role: "selectAll" }]);
  } else if (params.selectionText) {
    const selection = [{ role: "copy" }];
    if (platform === "darwin") {
      const normalized = params.selectionText.replace(/\s+/g, " ").trim();
      const excerpt = normalized.length > 25 ? normalized.slice(0, 25) + "…" : normalized;
      selection.push({ label: `Look Up '${excerpt}'`, click: () => webContents.showDefinitionForSelection() });
    }
    sections.push(selection);
  }
  return sections.flatMap((section, index) => index ? [{ type: "separator" }, ...section] : section);
}

function attachContextMenu(webContents) {
  const { Menu, BrowserWindow } = require("electron");
  webContents.on("context-menu", (_event, parameters) => {
    const template = buildMenuTemplate(parameters, process.platform, webContents);
    const window = BrowserWindow.fromWebContents(webContents);
    if (template.length && window) Menu.buildFromTemplate(template).popup({ window });
  });
}

module.exports = { buildMenuTemplate, attachContextMenu };

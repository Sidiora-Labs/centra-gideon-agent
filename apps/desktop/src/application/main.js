"use strict";

const electron = require("electron");
const { DesktopApplication } = require("./desktop-application");

new DesktopApplication(electron).run();

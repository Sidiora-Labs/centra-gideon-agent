on run argv
    if (count of argv) is not 3 then error "Expected command, configured handle, and configured display name"
    set operation to item 1 of argv
    set targetHandle to item 2 of argv
    set targetName to item 3 of argv
    if operation is not in {"probe", "call", "answer", "hangup"} then error "Unsupported operation"
    if operation is "call" then
        open location ("facetime-audio:" & targetHandle)
        return "requested"
    end if
    tell application "System Events"
        if not (exists process "FaceTime") then
            if operation is "probe" then return "idle"
            error "FaceTime is not running"
        end if
        tell process "FaceTime"
            set allWindows to every window
            if (count of allWindows) is 0 then
                if operation is "probe" then return "idle"
                error "No FaceTime window is visible"
            end if
            if operation is "probe" then return "unknown"
            set matchingWindows to {}
            repeat with candidate in allWindows
                set labels to {}
                repeat with node in entire contents of candidate
                    try
                        set end of labels to value of node as text
                    end try
                    try
                        set end of labels to name of node as text
                    end try
                end repeat
                if targetName is in labels or targetHandle is in labels then set end of matchingWindows to candidate
            end repeat
            if (count of matchingWindows) is not 1 then error "Configured caller identity cannot be uniquely verified"
            set matchingButtons to {}
            repeat with node in entire contents of item 1 of matchingWindows
                try
                    if role of node is "AXButton" then
                        set labelText to name of node as text
                        if operation is "answer" and labelText is "Accept" then set end of matchingButtons to node
                        if operation is "hangup" and labelText is "End" then set end of matchingButtons to node
                    end if
                end try
            end repeat
            if (count of matchingButtons) is not 1 then error "Exact FaceTime action unavailable; localized or changed UI requires manual control"
            perform action "AXPress" of item 1 of matchingButtons
        end tell
    end tell
    return "requested"
end run

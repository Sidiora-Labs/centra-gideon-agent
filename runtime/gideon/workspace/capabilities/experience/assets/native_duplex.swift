import AVFoundation
import Foundation

enum DuplexError: Error { case usage, permission, capture, playback }

func capture(_ path: String, _ seconds: Double) throws {
    let semaphore = DispatchSemaphore(value: 0)
    var allowed = false
    AVCaptureDevice.requestAccess(for: .audio) { granted in allowed = granted; semaphore.signal() }
    semaphore.wait()
    guard allowed else { throw DuplexError.permission }
    let url = URL(fileURLWithPath: path)
    let settings: [String: Any] = [
        AVFormatIDKey: Int(kAudioFormatLinearPCM), AVSampleRateKey: 16000.0,
        AVNumberOfChannelsKey: 1, AVLinearPCMBitDepthKey: 16,
        AVLinearPCMIsFloatKey: false, AVLinearPCMIsBigEndianKey: false
    ]
    let recorder = try AVAudioRecorder(url: url, settings: settings)
    recorder.prepareToRecord()
    guard recorder.record(forDuration: seconds) else { throw DuplexError.capture }
    RunLoop.current.run(until: Date().addingTimeInterval(seconds + 0.25))
    recorder.stop()
    guard FileManager.default.fileExists(atPath: path) else { throw DuplexError.capture }
}

func play(_ path: String) throws {
    let player = try AVAudioPlayer(contentsOf: URL(fileURLWithPath: path))
    player.prepareToPlay()
    guard player.play() else { throw DuplexError.playback }
    while player.isPlaying { RunLoop.current.run(until: Date().addingTimeInterval(0.05)) }
}

do {
    let args = CommandLine.arguments
    guard args.count >= 3 else { throw DuplexError.usage }
    if args[1] == "capture" {
        guard args.count == 4, let seconds = Double(args[3]), seconds >= 1, seconds <= 30 else { throw DuplexError.usage }
        try capture(args[2], seconds)
    } else if args[1] == "play" {
        guard args.count == 3 else { throw DuplexError.usage }
        try play(args[2])
    } else { throw DuplexError.usage }
    print("ok")
} catch {
    FileHandle.standardError.write(Data("native duplex failed: \(error)\n".utf8))
    exit(1)
}

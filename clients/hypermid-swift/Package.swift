// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "HypermidClient",
    platforms: [.macOS(.v13), .iOS(.v16)],
    products: [.library(name: "HypermidClient", targets: ["HypermidClient"])],
    dependencies: [
        .package(url: "https://github.com/apple/swift-crypto.git", from: "3.0.0")
    ],
    targets: [
        .target(
            name: "HypermidClient",
            dependencies: [.product(name: "Crypto", package: "swift-crypto")]
        )
    ]
)

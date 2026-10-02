// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "HypermidSwiftSDKHarness",
    dependencies: [.package(path: "../../../../clients/hypermid-swift")],
    targets: [
        .executableTarget(
            name: "HypermidSwiftSDKHarness",
            dependencies: [.product(name: "HypermidClient", package: "hypermid-swift")]
        )
    ]
)


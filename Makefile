.PHONY: build run project clean check

build:
	cd apps/macos && xcodebuild -project Talos.xcodeproj -scheme Talos -configuration Debug -derivedDataPath .build CODE_SIGNING_ALLOWED=NO build

run: build
	open apps/macos/.build/Build/Products/Debug/Talos.app

project:
	cd apps/macos && xcodegen generate

clean:
	rm -rf apps/macos/.build

check: build

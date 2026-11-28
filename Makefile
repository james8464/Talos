.PHONY: build cli run project clean check ax-audit semantic-eval semantic-heldout localization-check localization-coverage localization-eval

build:
	cd apps/macos && xcodebuild -project Talos.xcodeproj -scheme Talos -configuration Debug -derivedDataPath .build CODE_SIGNING_ALLOWED=NO build

cli:
	cd apps/macos && xcodebuild -project Talos.xcodeproj -scheme TalosCLI -configuration Debug -derivedDataPath .build CODE_SIGNING_ALLOWED=NO build

run: build
	open apps/macos/.build/Build/Products/Debug/Talos.app

project:
	cd apps/macos && xcodegen generate

clean:
	rm -rf apps/macos/.build

semantic-heldout:
	swiftc -O apps/macos/Talos/SemanticIndex.swift apps/macos/Tools/SemanticEval/HeldOut/main.swift -o /tmp/talos-semantic-heldout
	/tmp/talos-semantic-heldout .

localization-check:
	python3 apps/macos/Tools/localization_check.py

localization-coverage:
	python3 apps/macos/Tools/localization_coverage.py

localization-eval:
	swiftc apps/macos/Tools/LocalizationEval/main.swift -o /tmp/talos-localization-eval
	/tmp/talos-localization-eval

semantic-eval:
	swiftc -O apps/macos/Talos/SemanticIndex.swift apps/macos/Tools/SemanticEval/main.swift -o /tmp/talos-semantic-eval
	/tmp/talos-semantic-eval .

ax-audit:
	swiftc apps/macos/Tools/AXAudit.swift -o /tmp/talos-ax-audit
	/tmp/talos-ax-audit $$(pgrep -f "Talos.app/Contents/MacOS/Talos" | head -1)

check: build

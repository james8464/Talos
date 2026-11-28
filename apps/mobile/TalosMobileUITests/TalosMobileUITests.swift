import XCTest

final class TalosMobileUITests: XCTestCase {
    private var app: XCUIApplication!

    override func setUpWithError() throws {
        continueAfterFailure = false
        app = XCUIApplication()
        app.launchEnvironment["TALOS_REMOTE_SERVER"] =
            ProcessInfo.processInfo.environment["TALOS_REMOTE_SERVER"] ?? "http://127.0.0.1:8798"
        app.launchEnvironment["TALOS_REMOTE_TOKEN"] =
            ProcessInfo.processInfo.environment["TALOS_REMOTE_TOKEN"] ?? "remote-token"
        app.launch()
        XCTAssertTrue(
            app.navigationBars["Talos Remote"].waitForExistence(timeout: 20),
            "The session UI did not appear"
        )
    }

    func testSendStopAndApprove() throws {
        app.buttons["newSessionButton"].tap()
        XCTAssertTrue(waitForText(containing: "No messages yet", timeout: 20))

        type("Reply with exactly IOS-UI-OK and nothing else.")
        app.buttons["sendButton"].tap()
        XCTAssertTrue(
            waitForText(containing: "IOS-UI-OK", timeout: 90),
            "The model reply did not appear"
        )

        type("Write a very long 900 word essay about distributed systems. Do not stop early.")
        app.buttons["sendButton"].tap()
        XCTAssertTrue(app.buttons["stopButton"].waitForExistence(timeout: 20))
        app.buttons["stopButton"].tap()
        XCTAssertTrue(app.buttons["stopButton"].waitForNonExistence(timeout: 30))

        type("Use the shell tool now to run exactly: echo IOS-UI-APPROVAL-OK. Do not answer without calling the tool.")
        app.buttons["sendButton"].tap()
        XCTAssertTrue(
            app.staticTexts["Approval needed"].waitForExistence(timeout: 90),
            "The approval card did not appear"
        )
        app.buttons["allowButton"].tap()
        XCTAssertTrue(
            waitForText(containing: "IOS-UI-APPROVAL-OK", timeout: 90),
            "The approved tool result did not appear"
        )
    }

    private func type(_ text: String) {
        let field = app.textViews["promptField"].exists
            ? app.textViews["promptField"]
            : app.textFields["promptField"]
        XCTAssertTrue(field.waitForExistence(timeout: 20))
        field.tap()
        field.typeText(text)
    }

    private func waitForText(containing value: String, timeout: TimeInterval) -> Bool {
        let predicate = NSPredicate(format: "label CONTAINS[c] %@", value)
        let element = app.staticTexts.containing(predicate).firstMatch
        return element.waitForExistence(timeout: timeout)
    }
}

import XCTest
import TalosToolParser

final class ToolParserTests: XCTestCase {
    func testDeepSeekDSMLTags() {
        let text = """
        Let me inspect the file.

        <｜DSML｜tool_calls>
        <｜DSML｜invoke name="read_file">
        <｜DSML｜parameter name="path" string="true">/tmp/a&amp;b.swift</｜DSML｜parameter>
        <｜DSML｜parameter name="start_line" string="false">42</｜DSML｜parameter>
        <｜DSML｜parameter name="fuzzy" string="false">true</｜DSML｜parameter>
        </｜DSML｜invoke>
        </｜DSML｜tool_calls>
        """

        let parsed = ToolParser.extractAll(from: text)
        XCTAssertEqual(parsed.calls.count, 1)
        let call = try? XCTUnwrap(parsed.calls.first)
        XCTAssertEqual(call?.name, "read_file")
        XCTAssertEqual(call?.string("path"), "/tmp/a&b.swift")
        XCTAssertEqual(call?.int("start_line"), 42)
        XCTAssertTrue(call?.bool("fuzzy") ?? false)
        XCTAssertFalse(parsed.cleaned.contains("DSML"))
        XCTAssertTrue(parsed.cleaned.contains("Let me inspect the file."))
    }

    func testDSMLMultipleInvokes() {
        let text = """
        <｜DSML｜calls>
        <｜DSML｜invoke name="read_file">
        <｜DSML｜parameter name="path" string="true">a.swift</｜DSML｜parameter>
        </｜DSML｜invoke>
        <｜DSML｜invoke name="shell">
        <｜DSML｜parameter name="command" string="true">ls</｜DSML｜parameter>
        </｜DSML｜invoke>
        </｜DSML｜calls>
        """

        let parsed = ToolParser.extractAll(from: text)
        XCTAssertEqual(parsed.calls.map(\.name), ["read_file", "shell"])
        XCTAssertEqual(parsed.calls.last?.string("command"), "ls")
        XCTAssertTrue(parsed.cleaned.isEmpty)
    }

    func testXMLToolWrappers() {
        let text = """
        First read it.
        <tool>{"name":"read_file","path":"a.swift","start_line":3}</tool>
        Then run it.
        <tool_call name="shell">{"command":"ls -la"}</tool_call>
        """

        let parsed = ToolParser.extractAll(from: text)
        XCTAssertEqual(parsed.calls.map(\.name), ["read_file", "shell"])
        XCTAssertEqual(parsed.calls.first?.string("path"), "a.swift")
        XCTAssertEqual(parsed.calls.first?.int("start_line"), 3)
        XCTAssertEqual(parsed.calls.last?.string("command"), "ls -la")
        XCTAssertTrue(parsed.cleaned.contains("First read it."))
        XCTAssertTrue(parsed.cleaned.contains("Then run it."))
        XCTAssertFalse(parsed.cleaned.contains("<tool"))
    }

    func testMultipleCallsKeepTextualOrder() {
        let text = """
        One.
        ```tool
        {"name":"shell","command":"pwd"}
        ```
        Two.
        <｜DSML｜tool_calls>
        <｜DSML｜invoke name="read_file">
        <｜DSML｜parameter name="path" string="true">b.swift</｜DSML｜parameter>
        </｜DSML｜invoke>
        </｜DSML｜tool_calls>
        Three.
        <tool>{"query":"needle","glob":"*.swift"}</tool>
        """

        let parsed = ToolParser.extractAll(from: text)
        XCTAssertEqual(parsed.calls.map(\.name), ["shell", "read_file", "search_files"])
        XCTAssertEqual(parsed.calls.last?.string("query"), "needle")
        XCTAssertTrue(parsed.cleaned.contains("One."))
        XCTAssertTrue(parsed.cleaned.contains("Two."))
        XCTAssertTrue(parsed.cleaned.contains("Three."))
    }

    func testInfersMissingNamesFromSchema() {
        let cases: [(String, String)] = [
            (#"{"path":"a.swift","start_line":2}"#, "read_file"),
            (#"{"query":"needle","glob":"*.swift"}"#, "search_files"),
            (#"{"command":"ls"}"#, "shell"),
            (#"{"patch":"--- a"}"#, "apply_patch"),
            (#"{"path":"src","action":"list"}"#, "list_files"),
            (#"{"path":"a.txt","content":"x"}"#, "write_file"),
            (#"{"steps":[{"title":"x"}]}"#, "update_plan"),
        ]

        for (body, expected) in cases {
            let text = "```tool\n\(body)\n```"
            let parsed = ToolParser.extractAll(from: text)
            XCTAssertEqual(parsed.calls.first?.name, expected, "body: \(body)")
            XCTAssertEqual(parsed.calls.count, 1, "body: \(body)")
        }
    }

    func testMalformedToolFenceIsRemoved() {
        let text = """
        I will retry.
        ```tool
        {"name":"read_file", this is not json}
        ```
        """

        let parsed = ToolParser.extractAll(from: text)
        XCTAssertTrue(parsed.calls.isEmpty)
        XCTAssertFalse(parsed.cleaned.contains("```"))
        XCTAssertFalse(parsed.cleaned.contains("this is not json"))
        XCTAssertTrue(parsed.cleaned.contains("I will retry."))
    }

    func testIllustrativeJSONBlocksArePreserved() {
        let text = """
        Here is the shape:
        ```json
        {"example": true}
        ```
        """

        let parsed = ToolParser.extractAll(from: text)
        XCTAssertTrue(parsed.calls.isEmpty)
        XCTAssertTrue(parsed.cleaned.contains("```json"))
        XCTAssertTrue(parsed.cleaned.contains(#"{"example": true}"#))
    }

    func testJSONBlockWithoutNameIsNotExecuted() {
        let text = """
        Example only:
        ```json
        {"command":"rm -rf /tmp/nothing"}
        ```
        """

        let parsed = ToolParser.extractAll(from: text)
        XCTAssertTrue(parsed.calls.isEmpty)
        XCTAssertTrue(parsed.cleaned.contains("Example only:"))
        XCTAssertTrue(parsed.cleaned.contains("rm -rf"))
    }

    func testPlainTextIsUnchanged() {
        let text = "No tools here. Just an answer."
        let parsed = ToolParser.extractAll(from: text)
        XCTAssertTrue(parsed.calls.isEmpty)
        XCTAssertEqual(parsed.cleaned, text)
        XCTAssertNil(ToolParser.extract(from: text))
    }
}

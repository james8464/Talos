import Foundation
import AppKit
import CryptoKit
import Network

struct MCPServerConfig: Identifiable, Codable, Equatable, Sendable {
    var id = UUID()
    var name: String
    var command: String = ""
    var args: [String] = []
    var env: [String: String] = [:]
    var url: String? = nil
    var enabled = true
}

struct MCPTool: Identifiable, Hashable, Sendable {
    let serverID: UUID
    let serverName: String
    let name: String
    let description: String
    let inputSchema: String
    let uiResourceUri: String?

    var id: String { "\(serverID.uuidString)|\(name)" }

    var toolName: String {
        "mcp__\(serverName.normalizedToolComponent)__\(name.normalizedToolComponent)"
    }

    var promptDescription: String {
        let schema = inputSchema.count > 600 ? String(inputSchema.prefix(600)) + "…" : inputSchema
        return "\(description.isEmpty ? name : description) Arguments: \(schema)"
    }

    static func make(raw: [String: Any], config: MCPServerConfig) -> MCPTool? {
        guard let name = raw["name"] as? String else { return nil }
        let description = raw["description"] as? String ?? ""
        let schemaObject = raw["inputSchema"] as? [String: Any] ?? [:]
        let schemaData = try? JSONSerialization.data(withJSONObject: schemaObject, options: [.sortedKeys])
        let schema = schemaData.flatMap { String(data: $0, encoding: .utf8) } ?? "{}"
        let meta = raw["_meta"] as? [String: Any]
        let ui = meta?["ui"] as? [String: Any]
        let uiResource = (ui?["resourceUri"] as? String)
            ?? (meta?["ui/resourceUri"] as? String)
        return MCPTool(
            serverID: config.id,
            serverName: config.name,
            name: name,
            description: description,
            inputSchema: schema,
            uiResourceUri: uiResource
        )
    }
}

private extension String {
    var normalizedToolComponent: String {
        let allowed = CharacterSet.alphanumerics
        let scalars = lowercased().unicodeScalars.map { scalar -> Character in
            allowed.contains(scalar) ? Character(String(scalar)) : "_"
        }
        return String(scalars)
    }
}

struct MCPResourceContent: Sendable {
    let uri: String
    let mimeType: String?
    let text: String?

    var isHTML: Bool {
        (mimeType ?? "").lowercased().contains("html")
    }
}

struct MCPUIApp: Identifiable, Sendable {
    let id = UUID()
    let uri: String
    let serverName: String
    let title: String
    let mimeType: String
    let html: String
}

enum MCPElicitationKind: Equatable, Sendable {
    case string
    case number
    case integer
    case boolean
    case choice([String])
}

struct MCPElicitationField: Identifiable, Equatable, Sendable {
    let name: String
    let title: String
    let detail: String?
    let kind: MCPElicitationKind
    let required: Bool
    let defaultValue: String?

    var id: String { name }
}

struct MCPElicitationRequest: Identifiable, Equatable, Sendable {
    let id = UUID()
    let serverName: String
    let message: String
    let fields: [MCPElicitationField]

    static func make(serverName: String, params: [String: Any]) -> MCPElicitationRequest? {
        let message = params["message"] as? String ?? "The MCP server needs more information."
        guard let schema = params["requestedSchema"] as? [String: Any],
              let properties = schema["properties"] as? [String: Any] else {
            return MCPElicitationRequest(serverName: serverName, message: message, fields: [])
        }
        let required = Set(schema["required"] as? [String] ?? [])
        let fields = properties.keys.sorted().compactMap { key -> MCPElicitationField? in
            guard let raw = properties[key] as? [String: Any] else { return nil }
            let type = raw["type"] as? String ?? "string"
            let kind: MCPElicitationKind
            if let choices = raw["enum"] as? [String] {
                kind = .choice(choices)
            } else {
                switch type {
                case "number": kind = .number
                case "integer": kind = .integer
                case "boolean": kind = .boolean
                default: kind = .string
                }
            }
            let defaultText: String?
            if let value = raw["default"] as? String {
                defaultText = value
            } else if let value = raw["default"] as? NSNumber {
                defaultText = value.stringValue
            } else {
                defaultText = nil
            }
            return MCPElicitationField(
                name: key,
                title: raw["title"] as? String ?? key,
                detail: raw["description"] as? String,
                kind: kind,
                required: required.contains(key),
                defaultValue: defaultText
            )
        }
        return MCPElicitationRequest(serverName: serverName, message: message, fields: fields)
    }
}

enum MCPElicitationAction: String, Sendable {
    case accept
    case decline
    case cancel
}

struct MCPElicitationResponse: Sendable {
    var action: MCPElicitationAction
    var content: [String: String] = [:]

    static let decline = MCPElicitationResponse(action: .decline)
    static let cancel = MCPElicitationResponse(action: .cancel)

    /// Converts the string values collected by the UI into the JSON types the
    /// server's schema declares.
    func jsonContent(fields: [MCPElicitationField]) -> [String: Any] {
        guard action == .accept else { return [:] }
        var result: [String: Any] = [:]
        for field in fields {
            guard let raw = content[field.name] else { continue }
            switch field.kind {
            case .number:
                if let value = Double(raw) { result[field.name] = value }
            case .integer:
                if let value = Int(raw) { result[field.name] = value }
            case .boolean:
                result[field.name] = ["true", "yes", "1"].contains(raw.lowercased())
            case .string, .choice:
                result[field.name] = raw
            }
        }
        return result
    }
}


typealias MCPElicitationHandler = @Sendable (MCPElicitationRequest) async -> MCPElicitationResponse

// MARK: - OAuth

struct MCPOAuthTokens: Codable, Sendable {
    var accessToken: String
    var refreshToken: String?
    var expiresAt: Date?
    var tokenEndpoint: String
    var clientID: String
    var resource: String
}

protocol MCPTokenStore: Sendable {
    func load(key: String) -> MCPOAuthTokens?
    func save(_ tokens: MCPOAuthTokens, for key: String)
    func delete(key: String)
}

struct KeychainMCPTokenStore: MCPTokenStore {
    func load(key: String) -> MCPOAuthTokens? {
        guard let raw = Keychain.read(account: "mcp-oauth-\(key)"),
              let data = raw.data(using: .utf8) else { return nil }
        return try? JSONDecoder().decode(MCPOAuthTokens.self, from: data)
    }

    func save(_ tokens: MCPOAuthTokens, for key: String) {
        guard let data = try? JSONEncoder().encode(tokens),
              let raw = String(data: data, encoding: .utf8) else { return }
        try? Keychain.save(raw, account: "mcp-oauth-\(key)")
    }

    func delete(key: String) {
        Keychain.delete(account: "mcp-oauth-\(key)")
    }
}

enum PKCE {
    static func verifier() -> String {
        var bytes = [UInt8](repeating: 0, count: 32)
        _ = SecRandomCopyBytes(kSecRandomDefault, bytes.count, &bytes)
        return Data(bytes).base64URLEncodedString()
    }

    static func challenge(for verifier: String) -> String {
        let digest = SHA256.hash(data: Data(verifier.utf8))
        return Data(digest).base64URLEncodedString()
    }
}

private extension Data {
    func base64URLEncodedString() -> String {
        base64EncodedString()
            .replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
    }
}

/// Minimal loopback HTTP listener for the OAuth authorization response.
final class OAuthCallbackServer: @unchecked Sendable {
    private let listener: NWListener
    private let queue = DispatchQueue(label: "app.talos.mcp.oauth.callback")
    private var continuation: CheckedContinuation<URL, Error>?
    private var readyContinuation: CheckedContinuation<Void, Error>?
    private var connection: NWConnection?
    private(set) var port: UInt16?

    init() throws {
        let parameters = NWParameters.tcp
        parameters.allowLocalEndpointReuse = true
        listener = try NWListener(using: parameters, on: .any)
    }

    var redirectURI: String {
        "http://127.0.0.1:\(port ?? 0)/callback"
    }

    func start() async throws {
        listener.stateUpdateHandler = { [weak self] state in
            guard let self else { return }
            switch state {
            case .ready:
                self.port = self.listener.port?.rawValue
                self.readyContinuation?.resume()
                self.readyContinuation = nil
            case .failed(let error):
                self.readyContinuation?.resume(throwing: error)
                self.readyContinuation = nil
                self.continuation?.resume(throwing: error)
                self.continuation = nil
            default:
                break
            }
        }
        listener.newConnectionHandler = { [weak self] connection in
            self?.accept(connection)
        }
        listener.start(queue: queue)
        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
            readyContinuation = continuation
        }
    }

    func waitForCallback() async throws -> URL {
        try await withCheckedThrowingContinuation { continuation in
            self.continuation = continuation
        }
    }

    func cancel() {
        listener.cancel()
        connection?.cancel()
        continuation?.resume(throwing: MCPError.message("OAuth was cancelled."))
        continuation = nil
        readyContinuation?.resume(throwing: MCPError.message("OAuth was cancelled."))
        readyContinuation = nil
    }

    private func accept(_ connection: NWConnection) {
        self.connection = connection
        connection.start(queue: queue)
        connection.receive(minimumIncompleteLength: 1, maximumLength: 65_536) { [weak self] data, _, _, _ in
            guard let self else { return }
            let request = data.flatMap { String(data: $0, encoding: .utf8) } ?? ""
            let target = request
                .components(separatedBy: "\r\n")
                .first?
                .components(separatedBy: " ")
                .dropFirst()
                .first
            let url = target.flatMap { URL(string: "http://127.0.0.1" + $0) }
            let body = "<html><body style='font-family:-apple-system;padding:40px'><h2>Talos is connected</h2><p>You can close this window.</p></body></html>"
            let response = "HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: \(body.utf8.count)\r\nConnection: close\r\n\r\n\(body)"
            connection.send(content: Data(response.utf8), completion: .contentProcessed { _ in
                connection.cancel()
            })
            if let url, let continuation = self.continuation {
                self.continuation = nil
                continuation.resume(returning: url)
            } else {
                self.continuation?.resume(throwing: MCPError.message("OAuth returned an unreadable callback."))
                self.continuation = nil
            }
        }
    }
}

actor MCPOAuthClient {
    typealias Browser = @Sendable (URL) async -> URL?

    private struct Metadata: Sendable {
        let authorizationEndpoint: URL
        let tokenEndpoint: URL
        let registrationEndpoint: URL?
    }

    private let serverName: String
    private let store: MCPTokenStore
    private let browser: Browser?
    private var cache: [String: MCPOAuthTokens] = [:]

    init(
        serverName: String,
        store: MCPTokenStore = KeychainMCPTokenStore(),
        browser: Browser? = nil
    ) {
        self.serverName = serverName
        self.store = store
        self.browser = browser
    }

    func cachedToken(for resource: URL) async -> String? {
        let key = resource.absoluteString
        let tokens = cache[key] ?? store.load(key: key)
        guard let tokens else { return nil }
        cache[key] = tokens
        if let expires = tokens.expiresAt, expires.timeIntervalSinceNow < 60 {
            return try? await refresh(tokens, key: key).accessToken
        }
        return tokens.accessToken
    }

    func authorize(resource: URL, challenge: String?) async throws -> String {
        let key = resource.absoluteString
        if let tokens = cache[key] ?? store.load(key: key) {
            if let expires = tokens.expiresAt, expires.timeIntervalSinceNow >= 60 {
                return tokens.accessToken
            }
            if (try? await refresh(tokens, key: key)) != nil {
                return cache[key]?.accessToken ?? tokens.accessToken
            }
        }

        let metadata = try await discover(resource: resource, challenge: challenge)
        let callback = try OAuthCallbackServer()
        try await callback.start()
        defer { callback.cancel() }

        let redirectURI = callback.redirectURI
        let clientID = try await register(metadata: metadata, redirectURI: redirectURI)
        let verifier = PKCE.verifier()
        let state = PKCE.verifier()

        guard var components = URLComponents(url: metadata.authorizationEndpoint, resolvingAgainstBaseURL: false) else {
            throw MCPError.message("\(serverName) returned an invalid authorization endpoint.")
        }
        var items = components.queryItems ?? []
        items.append(contentsOf: [
            URLQueryItem(name: "response_type", value: "code"),
            URLQueryItem(name: "client_id", value: clientID),
            URLQueryItem(name: "redirect_uri", value: redirectURI),
            URLQueryItem(name: "code_challenge", value: PKCE.challenge(for: verifier)),
            URLQueryItem(name: "code_challenge_method", value: "S256"),
            URLQueryItem(name: "state", value: state),
            URLQueryItem(name: "resource", value: resource.absoluteString),
        ])
        components.queryItems = items
        guard let authorizationURL = components.url else {
            throw MCPError.message("Could not build the authorization URL.")
        }

        let callbackURL: URL?
        if let browser {
            callbackURL = await browser(authorizationURL)
        } else {
            await MainActor.run {
                NSWorkspace.shared.open(authorizationURL)
            }
            callbackURL = try await callback.waitForCallback()
        }
        guard let callbackURL,
              let returned = URLComponents(url: callbackURL, resolvingAgainstBaseURL: false) else {
            throw MCPError.message("OAuth was cancelled.")
        }
        let parameters = Dictionary(
            uniqueKeysWithValues: (returned.queryItems ?? []).map { ($0.name, $0.value ?? "") }
        )
        if let error = parameters["error"] {
            throw MCPError.message("OAuth failed: \(error).")
        }
        guard parameters["state"] == state else {
            throw MCPError.message("OAuth state did not match.")
        }
        guard let code = parameters["code"] else {
            throw MCPError.message("OAuth returned no authorization code.")
        }

        var tokens = try await exchange(
            code: code,
            verifier: verifier,
            redirectURI: redirectURI,
            clientID: clientID,
            tokenEndpoint: metadata.tokenEndpoint,
            resource: resource,
            previous: nil
        )
        tokens.resource = resource.absoluteString
        cache[key] = tokens
        store.save(tokens, for: key)
        return tokens.accessToken
    }

    private func refresh(_ tokens: MCPOAuthTokens, key: String) async throws -> MCPOAuthTokens {
        guard let refreshToken = tokens.refreshToken,
              let endpoint = URL(string: tokens.tokenEndpoint) else {
            throw MCPError.message("The MCP token expired and cannot be refreshed.")
        }
        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.httpBody = form([
            "grant_type": "refresh_token",
            "refresh_token": refreshToken,
            "client_id": tokens.clientID,
            "resource": tokens.resource,
        ])
        let (data, response) = try await URLSession.shared.data(for: request)
        try validate(response, data: data, label: "token refresh")
        var updated = try parseTokens(data, fallbackClientID: tokens.clientID, endpoint: endpoint.absoluteString)
        updated.resource = tokens.resource
        if updated.refreshToken == nil { updated.refreshToken = tokens.refreshToken }
        cache[key] = updated
        store.save(updated, for: key)
        return updated
    }

    private func discover(resource: URL, challenge: String?) async throws -> Metadata {
        var candidates: [URL] = []
        if let metadataURL = Self.resourceMetadataURL(from: challenge) {
            candidates.append(metadataURL)
        }
        if let scheme = resource.scheme, let host = resource.host {
            var base = URLComponents()
            base.scheme = scheme
            base.host = host
            if let port = resource.port { base.port = port }
            let trimmedPath = resource.path.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
            let suffix = trimmedPath.isEmpty ? "" : "/" + trimmedPath
            if let standard = URL(string: "\(scheme)://\(host)\(resource.port.map { ":\($0)" } ?? "")/.well-known/oauth-protected-resource\(suffix)") {
                candidates.append(standard)
            }
            if let plain = URL(string: "\(scheme)://\(host)\(resource.port.map { ":\($0)" } ?? "")/.well-known/oauth-protected-resource") {
                candidates.append(plain)
            }
        }

        var authorizationServers: [URL] = []
        for candidate in candidates {
            guard let object = try? await fetchJSON(candidate) else { continue }
            authorizationServers = (object["authorization_servers"] as? [String] ?? [])
                .compactMap(URL.init(string:))
            if !authorizationServers.isEmpty { break }
        }
        if authorizationServers.isEmpty, let scheme = resource.scheme, let host = resource.host {
            var components = URLComponents()
            components.scheme = scheme
            components.host = host
            components.port = resource.port
            if let url = components.url { authorizationServers = [url] }
        }
        guard let issuer = authorizationServers.first else {
            throw MCPError.message("\(serverName) did not publish an OAuth authorization server.")
        }

        var metadataCandidates: [URL] = []
        let issuerText = issuer.absoluteString.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        metadataCandidates.append(URL(string: issuerText + "/.well-known/oauth-authorization-server")!)
        metadataCandidates.append(URL(string: issuerText + "/.well-known/openid-configuration")!)
        if let path = issuer.path.isEmpty ? nil : issuer.path {
            metadataCandidates.append(URL(string: "\(issuer.scheme ?? "https")://\(issuer.host ?? "")/.well-known/oauth-authorization-server\(path)")!)
        }

        for candidate in metadataCandidates {
            guard let object = try? await fetchJSON(candidate),
                  let authorization = object["authorization_endpoint"] as? String,
                  let authorizationURL = URL(string: authorization),
                  let token = object["token_endpoint"] as? String,
                  let tokenURL = URL(string: token) else { continue }
            return Metadata(
                authorizationEndpoint: authorizationURL,
                tokenEndpoint: tokenURL,
                registrationEndpoint: (object["registration_endpoint"] as? String).flatMap(URL.init(string:))
            )
        }
        throw MCPError.message("\(serverName) did not publish usable OAuth metadata.")
    }

    private func register(metadata: Metadata, redirectURI: String) async throws -> String {
        guard let endpoint = metadata.registrationEndpoint else {
            throw MCPError.message("\(serverName) requires OAuth but does not support dynamic client registration.")
        }
        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.httpBody = try JSONSerialization.data(withJSONObject: [
            "client_name": "Talos",
            "redirect_uris": [redirectURI],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        ])
        let (data, response) = try await URLSession.shared.data(for: request)
        try validate(response, data: data, label: "client registration")
        guard let object = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              let clientID = object["client_id"] as? String else {
            throw MCPError.message("\(serverName) returned no OAuth client ID.")
        }
        return clientID
    }

    private func exchange(
        code: String,
        verifier: String,
        redirectURI: String,
        clientID: String,
        tokenEndpoint: URL,
        resource: URL,
        previous: MCPOAuthTokens?
    ) async throws -> MCPOAuthTokens {
        var request = URLRequest(url: tokenEndpoint)
        request.httpMethod = "POST"
        request.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.httpBody = form([
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirectURI,
            "client_id": clientID,
            "code_verifier": verifier,
            "resource": resource.absoluteString,
        ])
        let (data, response) = try await URLSession.shared.data(for: request)
        try validate(response, data: data, label: "token exchange")
        return try parseTokens(data, fallbackClientID: clientID, endpoint: tokenEndpoint.absoluteString)
    }

    private func parseTokens(_ data: Data, fallbackClientID: String, endpoint: String) throws -> MCPOAuthTokens {
        guard let object = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              let access = object["access_token"] as? String else {
            throw MCPError.message("\(serverName) returned no OAuth access token.")
        }
        let expires = (object["expires_in"] as? NSNumber).map {
            Date().addingTimeInterval($0.doubleValue)
        }
        return MCPOAuthTokens(
            accessToken: access,
            refreshToken: object["refresh_token"] as? String,
            expiresAt: expires,
            tokenEndpoint: object["token_endpoint"] as? String ?? endpoint,
            clientID: object["client_id"] as? String ?? fallbackClientID,
            resource: ""
        )
    }

    private func fetchJSON(_ url: URL) async throws -> [String: Any] {
        var request = URLRequest(url: url)
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        let (data, response) = try await URLSession.shared.data(for: request)
        try validate(response, data: data, label: url.path)
        return (try JSONSerialization.jsonObject(with: data) as? [String: Any]) ?? [:]
    }

    private func validate(_ response: URLResponse, data: Data, label: String) throws {
        guard let http = response as? HTTPURLResponse else {
            throw MCPError.message("\(serverName) returned no response during \(label).")
        }
        guard (200..<300).contains(http.statusCode) else {
            let body = String(decoding: data, as: UTF8.self)
            let clipped = body.count > 400 ? String(body.prefix(400)) : body
            throw MCPError.message("\(serverName) OAuth \(label) failed with \(http.statusCode). \(clipped)")
        }
    }

    private func form(_ values: [String: String]) -> Data {
        let allowed = CharacterSet.alphanumerics.union(CharacterSet(charactersIn: "-._~"))
        let text = values.map { key, value in
            let encoded = value.addingPercentEncoding(withAllowedCharacters: allowed) ?? value
            return "\(key)=\(encoded)"
        }.joined(separator: "&")
        return Data(text.utf8)
    }

    private static func resourceMetadataURL(from challenge: String?) -> URL? {
        guard let challenge else { return nil }
        guard let range = challenge.range(of: #"resource_metadata="([^"]+)""#, options: .regularExpression) else {
            return nil
        }
        let match = String(challenge[range])
        let value = match
            .replacingOccurrences(of: "resource_metadata=", with: "")
            .trimmingCharacters(in: CharacterSet(charactersIn: "\""))
        return URL(string: value)
    }
}

struct MCPSamplingImage: Sendable {
    let mediaType: String
    let base64: String
}

struct MCPSamplingMessage: Sendable {
    let role: String
    let text: String
    let images: [MCPSamplingImage]
}

struct MCPSamplingRequest: Identifiable, Sendable {
    let id = UUID()
    let serverName: String
    let messages: [MCPSamplingMessage]
    let systemPrompt: String?
    let maxTokens: Int?
    let temperature: Double?
    let modelHints: String?

    var promptPreview: String {
        messages.map { "\($0.role): \($0.text)" }.joined(separator: "\n\n")
    }

    static func make(serverName: String, params: [String: Any]) -> MCPSamplingRequest? {
        guard let rawMessages = params["messages"] as? [[String: Any]], !rawMessages.isEmpty else {
            return nil
        }
        var messages: [MCPSamplingMessage] = []
        for raw in rawMessages {
            guard let role = raw["role"] as? String else { continue }
            var text = ""
            var images: [MCPSamplingImage] = []
            if let value = raw["content"] as? String {
                text = value
            } else if let block = raw["content"] as? [String: Any] {
                parse(block, text: &text, images: &images)
            } else if let blocks = raw["content"] as? [[String: Any]] {
                for block in blocks {
                    parse(block, text: &text, images: &images)
                }
            }
            messages.append(MCPSamplingMessage(role: role, text: text, images: images))
        }
        let preferences = params["modelPreferences"] as? [String: Any]
        let hints = (preferences?["hints"] as? [[String: Any]] ?? [])
            .compactMap { $0["name"] as? String }
        return MCPSamplingRequest(
            serverName: serverName,
            messages: messages,
            systemPrompt: params["systemPrompt"] as? String,
            maxTokens: (params["maxTokens"] as? NSNumber)?.intValue,
            temperature: (params["temperature"] as? NSNumber)?.doubleValue,
            modelHints: hints.isEmpty ? nil : hints.joined(separator: ", ")
        )
    }

    private static func parse(
        _ block: [String: Any],
        text: inout String,
        images: inout [MCPSamplingImage]
    ) {
        switch block["type"] as? String {
        case "text":
            if let value = block["text"] as? String {
                text += text.isEmpty ? value : "\n" + value
            }
        case "image":
            if let data = block["data"] as? String,
               let mediaType = block["mimeType"] as? String {
                images.append(MCPSamplingImage(mediaType: mediaType, base64: data))
            }
        default:
            break
        }
    }
}

enum MCPSamplingApproval: Sendable {
    case once
    case session
    case deny
}

enum MCPSamplingResult: Sendable {
    case accept(text: String, model: String)
    case decline
    case cancel
    case failure(String)
}

typealias MCPSamplingHandler = @Sendable (MCPSamplingRequest) async -> MCPSamplingResult

/// Holds the current sampling handler so live connections always use the
/// newest approval/model bridge.
actor MCPSamplingBroker {
    private var handler: MCPSamplingHandler?

    func set(_ handler: MCPSamplingHandler?) {
        self.handler = handler
    }

    func handle(_ request: MCPSamplingRequest) async -> MCPSamplingResult {
        guard let handler else { return .decline }
        return await handler(request)
    }
}

/// Holds the current elicitation handler so a running connection always sees
/// the newest UI bridge instead of the handler captured at connect time.
actor MCPElicitationBroker {
    private var handler: MCPElicitationHandler?

    func set(_ handler: MCPElicitationHandler?) {
        self.handler = handler
    }

    func handle(_ request: MCPElicitationRequest) async -> MCPElicitationResponse {
        guard let handler else { return .decline }
        return await handler(request)
    }
}


enum MCPError: LocalizedError {
    case message(String)

    var errorDescription: String? {
        switch self {
        case .message(let value): return value
        }
    }
}

actor MCPClient {
    private let config: MCPServerConfig
    private let elicit: MCPElicitationBroker?
    private let sampling: MCPSamplingBroker?
    private var process: Process?
    private var input: FileHandle?
    private var output: FileHandle?
    private var reader: Task<Void, Never>?
    private var pending: [Int: CheckedContinuation<[String: Any], Error>] = [:]
    private var nextID = 1
    private var stopped = false

    init(
        config: MCPServerConfig,
        elicit: MCPElicitationBroker? = nil,
        sampling: MCPSamplingBroker? = nil
    ) {
        self.config = config
        self.elicit = elicit
        self.sampling = sampling
    }

    deinit {
        process?.terminate()
    }

    func start() async throws {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: config.command)
        process.arguments = config.args
        process.environment = ProcessInfo.processInfo.environment.merging(config.env) { _, new in new }

        let inputPipe = Pipe()
        let outputPipe = Pipe()
        let errorPipe = Pipe()
        process.standardInput = inputPipe
        process.standardOutput = outputPipe
        process.standardError = errorPipe

        do {
            try process.run()
        } catch {
            throw MCPError.message("Could not start \(config.name): \(error.localizedDescription)")
        }
        self.process = process
        self.input = inputPipe.fileHandleForWriting
        self.output = outputPipe.fileHandleForReading
        reader = Task { [weak self] in
            await self?.readLoop()
        }

        let response = try await request("initialize", params: [
            "protocolVersion": "2024-11-05",
            "capabilities": ["elicitation": [:], "sampling": [:]],
            "clientInfo": ["name": "Talos", "version": "0.6"],
        ])
        guard response["result"] is [String: Any] else {
            throw MCPError.message("\(config.name) rejected the MCP initialize handshake.")
        }
        try sendNotification("notifications/initialized")
    }

    func listTools() async throws -> [MCPTool] {
        let response = try await request("tools/list")
        guard let result = response["result"] as? [String: Any],
              let rawTools = result["tools"] as? [[String: Any]] else {
            throw MCPError.message("\(config.name) did not return a tools list.")
        }
        return rawTools.compactMap { MCPTool.make(raw: $0, config: config) }
    }

    func callTool(name: String, arguments: [String: Any]) async throws -> String {
        let response = try await request("tools/call", params: [
            "name": name,
            "arguments": arguments,
        ])
        guard let result = response["result"] as? [String: Any] else {
            throw MCPError.message("No result from \(config.name).")
        }
        if let isError = result["isError"] as? Bool, isError {
            throw MCPError.message(renderContent(result["content"]))
        }
        return renderContent(result["content"])
    }

    func readResource(uri: String) async throws -> MCPResourceContent {
        let response = try await request("resources/read", params: ["uri": uri])
        guard let result = response["result"] as? [String: Any],
              let contents = result["contents"] as? [[String: Any]],
              let first = contents.first else {
            throw MCPError.message("\(config.name) returned no resource content.")
        }
        var text = first["text"] as? String
        if text == nil, let blob = first["blob"] as? String,
           let data = Data(base64Encoded: blob) {
            text = String(data: data, encoding: .utf8)
        }
        return MCPResourceContent(
            uri: first["uri"] as? String ?? uri,
            mimeType: first["mimeType"] as? String,
            text: text
        )
    }

    func stop() {
        stopped = true
        reader?.cancel()
        reader = nil
        process?.terminate()
        process = nil
        input = nil
        output = nil
        failPending(MCPError.message("MCP server \(config.name) stopped."))
    }

    private func renderContent(_ raw: Any?) -> String {
        guard let items = raw as? [[String: Any]] else {
            return String(describing: raw ?? "No content")
        }
        return items.compactMap { item -> String? in
            if let text = item["text"] as? String { return text }
            if let resource = item["resource"] as? [String: Any],
               let text = resource["text"] as? String { return text }
            return nil
        }.joined(separator: "\n")
    }

    private func request(_ method: String, params: [String: Any]? = nil) async throws -> [String: Any] {
        if stopped { throw MCPError.message("MCP server \(config.name) is stopped.") }
        let id = nextID
        nextID += 1
        let message: [String: Any] = [
            "jsonrpc": "2.0",
            "id": id,
            "method": method,
            "params": params ?? [:],
        ]
        return try await withCheckedThrowingContinuation { continuation in
            pending[id] = continuation
            do {
                try send(message)
            } catch {
                pending.removeValue(forKey: id)
                continuation.resume(throwing: error)
                return
            }
            Task { [weak self] in
                try? await Task.sleep(nanoseconds: 20_000_000_000)
                await self?.timeout(id)
            }
        }
    }

    private func timeout(_ id: Int) {
        guard let continuation = pending.removeValue(forKey: id) else { return }
        continuation.resume(throwing: MCPError.message("\(config.name) did not answer in time."))
    }

    private func sendNotification(_ method: String) throws {
        try send(["jsonrpc": "2.0", "method": method])
    }

    private func send(_ object: [String: Any]) throws {
        guard let input else {
            throw MCPError.message("\(config.name) is not running.")
        }
        let data = try JSONSerialization.data(withJSONObject: object)
        input.write(data)
        input.write(Data([0x0A]))
    }

    private func readLoop() async {
        guard let output else { return }
        do {
            for try await line in output.bytes.lines {
                guard !line.isEmpty, let data = line.data(using: .utf8) else { continue }
                guard let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { continue }
                if let method = object["method"] as? String {
                    if let rawID = object["id"] as? NSNumber {
                        let params = object["params"] as? [String: Any] ?? [:]
                        Task { [weak self] in
                            await self?.handleServerRequest(
                                id: rawID.intValue,
                                method: method,
                                params: params
                            )
                        }
                    }
                    continue
                }
                if let rawID = object["id"] as? NSNumber {
                    let id = rawID.intValue
                    if let continuation = pending.removeValue(forKey: id) {
                        if let error = object["error"] as? [String: Any] {
                            let message = error["message"] as? String ?? "MCP error"
                            continuation.resume(throwing: MCPError.message(message))
                        } else {
                            continuation.resume(returning: object)
                        }
                    }
                }
            }
        } catch {
            // The process ended or the pipe closed.
        }
        failPending(MCPError.message("MCP server \(config.name) closed its output."))
    }

    private func handleServerRequest(id: Int, method: String, params: [String: Any]) async {
        switch method {
        case "elicitation/create":
            let request = MCPElicitationRequest.make(serverName: config.name, params: params)
            let response: MCPElicitationResponse
            if let request, let elicit {
                response = await elicit.handle(request)
            } else {
                response = .decline
            }
            let content = request.map { response.jsonContent(fields: $0.fields) } ?? [:]
            try? sendResult(id: id, result: [
                "action": response.action.rawValue,
                "content": content,
            ])
        case "sampling/createMessage":
            guard let request = MCPSamplingRequest.make(serverName: config.name, params: params),
                  let sampling else {
                try? sendResult(id: id, error: [
                    "code": -32601,
                    "message": "Sampling is not available in Talos.",
                ])
                return
            }
            switch await sampling.handle(request) {
            case .accept(let text, let model):
                try? sendResult(id: id, result: [
                    "role": "assistant",
                    "content": ["type": "text", "text": text],
                    "model": model,
                    "stopReason": "endTurn",
                ])
            case .decline:
                try? sendResult(id: id, error: [
                    "code": -1,
                    "message": "The user declined the sampling request.",
                ])
            case .cancel:
                try? sendResult(id: id, error: [
                    "code": -1,
                    "message": "The sampling request was cancelled.",
                ])
            case .failure(let message):
                try? sendResult(id: id, error: ["code": -1, "message": message])
            }
        case "ping":
            try? sendResult(id: id, result: [:])
        case "roots/list":
            try? sendResult(id: id, result: ["roots": []])
        default:
            try? sendResult(id: id, error: [
                "code": -32601,
                "message": "Talos does not support \(method).",
            ])
        }
    }

    private func sendResult(id: Int, result: [String: Any]) throws {
        try send(["jsonrpc": "2.0", "id": id, "result": result])
    }

    private func sendResult(id: Int, error: [String: Any]) throws {
        try send(["jsonrpc": "2.0", "id": id, "error": error])
    }

    private func failPending(_ error: Error) {
        let continuations = pending.values
        pending.removeAll()
        for continuation in continuations {
            continuation.resume(throwing: error)
        }
    }
}

actor MCPHTTPClient {
    private let config: MCPServerConfig
    private let endpoint: URL
    private let elicit: MCPElicitationBroker?
    private let sampling: MCPSamplingBroker?
    private let oauth: MCPOAuthClient?
    private var sessionID: String?
    private var accessToken: String?
    private var nextID = 1

    init(
        config: MCPServerConfig,
        endpoint: URL,
        elicit: MCPElicitationBroker? = nil,
        sampling: MCPSamplingBroker? = nil,
        oauth: MCPOAuthClient? = nil
    ) {
        self.config = config
        self.endpoint = endpoint
        self.elicit = elicit
        self.sampling = sampling
        self.oauth = oauth
    }

    func start() async throws {
        let response = try await request("initialize", params: [
            "protocolVersion": "2024-11-05",
            "capabilities": ["elicitation": [:], "sampling": [:]],
            "clientInfo": ["name": "Talos", "version": "0.6"],
        ])
        guard response?["result"] is [String: Any] else {
            throw MCPError.message("\(config.name) rejected the MCP initialize handshake.")
        }
        try await notify("notifications/initialized")
    }

    func listTools() async throws -> [MCPTool] {
        guard let response = try await request("tools/list"),
              let result = response["result"] as? [String: Any],
              let rawTools = result["tools"] as? [[String: Any]] else {
            throw MCPError.message("\(config.name) did not return a tools list.")
        }
        return rawTools.compactMap { MCPTool.make(raw: $0, config: config) }
    }

    func callTool(name: String, arguments: [String: Any]) async throws -> String {
        guard let response = try await request("tools/call", params: [
            "name": name,
            "arguments": arguments,
        ]), let result = response["result"] as? [String: Any] else {
            throw MCPError.message("No result from \(config.name).")
        }
        if let isError = result["isError"] as? Bool, isError {
            throw MCPError.message(renderContent(result["content"]))
        }
        return renderContent(result["content"])
    }

    func readResource(uri: String) async throws -> MCPResourceContent {
        guard let response = try await request("resources/read", params: ["uri": uri]),
              let result = response["result"] as? [String: Any],
              let contents = result["contents"] as? [[String: Any]],
              let first = contents.first else {
            throw MCPError.message("\(config.name) returned no resource content.")
        }
        var text = first["text"] as? String
        if text == nil, let blob = first["blob"] as? String,
           let data = Data(base64Encoded: blob) {
            text = String(data: data, encoding: .utf8)
        }
        return MCPResourceContent(
            uri: first["uri"] as? String ?? uri,
            mimeType: first["mimeType"] as? String,
            text: text
        )
    }

    func stop() {}

    private func notify(_ method: String) async throws {
        _ = try await send(["jsonrpc": "2.0", "method": method])
    }

    private func request(_ method: String, params: [String: Any]? = nil) async throws -> [String: Any]? {
        let id = nextID
        nextID += 1
        return try await send([
            "jsonrpc": "2.0",
            "id": id,
            "method": method,
            "params": params ?? [:],
        ])
    }

    private func perform(
        _ object: [String: Any],
        token: String?
    ) async throws -> (Data, HTTPURLResponse) {
        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.timeoutInterval = 30
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json, text/event-stream", forHTTPHeaderField: "Accept")
        request.setValue("2024-11-05", forHTTPHeaderField: "MCP-Protocol-Version")
        if let sessionID {
            request.setValue(sessionID, forHTTPHeaderField: "Mcp-Session-Id")
        }
        if let token {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        request.httpBody = try JSONSerialization.data(withJSONObject: object)
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw MCPError.message("\(config.name) returned no response.")
        }
        return (data, http)
    }

    private func send(_ object: [String: Any]) async throws -> [String: Any]? {
        var token = accessToken
        if token == nil, let oauth {
            token = await oauth.cachedToken(for: endpoint)
        }
        var (data, http) = try await perform(object, token: token)
        if http.statusCode == 401, let oauth {
            let challenge = http.value(forHTTPHeaderField: "WWW-Authenticate")
            token = try await oauth.authorize(resource: endpoint, challenge: challenge)
            accessToken = token
            (data, http) = try await perform(object, token: token)
        }
        if let id = http.value(forHTTPHeaderField: "Mcp-Session-Id") {
            sessionID = id
        }
        guard (200..<300).contains(http.statusCode) else {
            throw MCPError.message("\(config.name) answered \(http.statusCode).")
        }
        guard !data.isEmpty else { return nil }
        let contentType = http.value(forHTTPHeaderField: "Content-Type") ?? ""
        if contentType.contains("text/event-stream") {
            let text = String(decoding: data, as: UTF8.self)
            let requestID = object["id"] as? Int
            var events: [[String: Any]] = []
            for line in text.components(separatedBy: "\n") {
                guard line.hasPrefix("data:") else { continue }
                let payload = line.dropFirst(5).trimmingCharacters(in: .whitespaces)
                if let payloadData = payload.data(using: .utf8),
                   let event = try? JSONSerialization.jsonObject(with: payloadData) as? [String: Any] {
                    events.append(event)
                }
            }
            for event in events {
                if let method = event["method"] as? String,
                   let rawID = event["id"] as? NSNumber {
                    await handleServerRequest(
                        id: rawID.intValue,
                        method: method,
                        params: event["params"] as? [String: Any] ?? [:]
                    )
                    continue
                }
                if let rawID = event["id"] as? NSNumber {
                    if requestID == nil || rawID.intValue == requestID {
                        return event
                    }
                }
            }
            throw MCPError.message("\(config.name) sent no JSON-RPC event.")
        }
        return try JSONSerialization.jsonObject(with: data) as? [String: Any]
    }

    private func handleServerRequest(id: Int, method: String, params: [String: Any]) async {
        switch method {
        case "elicitation/create":
            let request = MCPElicitationRequest.make(serverName: config.name, params: params)
            let response: MCPElicitationResponse
            if let request, let elicit {
                response = await elicit.handle(request)
            } else {
                response = .decline
            }
            let content = request.map { response.jsonContent(fields: $0.fields) } ?? [:]
            try? await send([
                "jsonrpc": "2.0",
                "id": id,
                "result": ["action": response.action.rawValue, "content": content],
            ])
        case "sampling/createMessage":
            guard let request = MCPSamplingRequest.make(serverName: config.name, params: params),
                  let sampling else {
                try? await send([
                    "jsonrpc": "2.0",
                    "id": id,
                    "error": ["code": -32601, "message": "Sampling is not available in Talos."],
                ])
                return
            }
            switch await sampling.handle(request) {
            case .accept(let text, let model):
                try? await send([
                    "jsonrpc": "2.0",
                    "id": id,
                    "result": [
                        "role": "assistant",
                        "content": ["type": "text", "text": text],
                        "model": model,
                        "stopReason": "endTurn",
                    ],
                ])
            case .decline:
                try? await send([
                    "jsonrpc": "2.0",
                    "id": id,
                    "error": ["code": -1, "message": "The user declined the sampling request."],
                ])
            case .cancel:
                try? await send([
                    "jsonrpc": "2.0",
                    "id": id,
                    "error": ["code": -1, "message": "The sampling request was cancelled."],
                ])
            case .failure(let message):
                try? await send([
                    "jsonrpc": "2.0",
                    "id": id,
                    "error": ["code": -1, "message": message],
                ])
            }
        case "ping":
            try? await send(["jsonrpc": "2.0", "id": id, "result": [:]])
        case "roots/list":
            try? await send(["jsonrpc": "2.0", "id": id, "result": ["roots": []]])
        default:
            try? await send([
                "jsonrpc": "2.0",
                "id": id,
                "error": ["code": -32601, "message": "Talos does not support \(method)."],
            ])
        }
    }

    private func renderContent(_ raw: Any?) -> String {
        guard let items = raw as? [[String: Any]] else {
            return String(describing: raw ?? "No content")
        }
        return items.compactMap { item -> String? in
            if let text = item["text"] as? String { return text }
            if let resource = item["resource"] as? [String: Any],
               let text = resource["text"] as? String { return text }
            return nil
        }.joined(separator: "\n")
    }
}

actor MCPManager {
    private enum Connection: Sendable {
        case stdio(MCPClient)
        case http(MCPHTTPClient)

        func stop() async {
            switch self {
            case .stdio(let client): await client.stop()
            case .http(let client): await client.stop()
            }
        }

        func listTools() async throws -> [MCPTool] {
            switch self {
            case .stdio(let client): return try await client.listTools()
            case .http(let client): return try await client.listTools()
            }
        }

        func callTool(name: String, arguments: [String: Any]) async throws -> String {
            switch self {
            case .stdio(let client): return try await client.callTool(name: name, arguments: arguments)
            case .http(let client): return try await client.callTool(name: name, arguments: arguments)
            }
        }

        func readResource(uri: String) async throws -> MCPResourceContent {
            switch self {
            case .stdio(let client): return try await client.readResource(uri: uri)
            case .http(let client): return try await client.readResource(uri: uri)
            }
        }
    }

    private var clients: [UUID: Connection] = [:]
    private(set) var tools: [MCPTool] = []
    private let elicit = MCPElicitationBroker()
    private let sampling = MCPSamplingBroker()

    func setElicitationHandler(_ handler: MCPElicitationHandler?) async {
        await elicit.set(handler)
    }

    func setSamplingHandler(_ handler: MCPSamplingHandler?) async {
        await sampling.set(handler)
    }

    func connect(_ configs: [MCPServerConfig]) async -> (tools: [MCPTool], errors: [String]) {
        for client in clients.values {
            await client.stop()
        }
        clients.removeAll()

        var discovered: [MCPTool] = []
        var errors: [String] = []
        for config in configs where config.enabled {
            let hasURL = config.url?.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty == false
            guard hasURL || !config.command.isEmpty else { continue }
            do {
                let connection: Connection
                let serverTools: [MCPTool]
                if let rawURL = config.url, let url = URL(string: rawURL), hasURL {
                    let client = MCPHTTPClient(
                        config: config,
                        endpoint: url,
                        elicit: elicit,
                        sampling: sampling,
                        oauth: MCPOAuthClient(serverName: config.name)
                    )
                    try await client.start()
                    serverTools = try await client.listTools()
                    connection = .http(client)
                } else {
                    let client = MCPClient(config: config, elicit: elicit, sampling: sampling)
                    try await client.start()
                    serverTools = try await client.listTools()
                    connection = .stdio(client)
                }
                clients[config.id] = connection
                discovered.append(contentsOf: serverTools)
            } catch {
                errors.append("\(config.name): \(error.localizedDescription)")
            }
        }
        tools = discovered
        return (discovered, errors)
    }

    func readResource(uri: String) async -> Result<MCPResourceContent, Error> {
        guard let tool = tools.first(where: { $0.uiResourceUri == uri }),
              let client = clients[tool.serverID] else {
            return .failure(MCPError.message("No connected server advertises \(uri)."))
        }
        do {
            return .success(try await client.readResource(uri: uri))
        } catch {
            return .failure(error)
        }
    }

    func call(toolName: String, arguments: [String: Any]) async -> Result<String, Error> {
        guard let tool = tools.first(where: { $0.toolName == toolName }),
              let client = clients[tool.serverID] else {
            return .failure(MCPError.message("No MCP client is connected for \(toolName)."))
        }
        do {
            return .success(try await client.callTool(name: tool.name, arguments: arguments))
        } catch {
            return .failure(error)
        }
    }

    func stopAll() async {
        for client in clients.values {
            await client.stop()
        }
        clients.removeAll()
        tools = []
    }
}

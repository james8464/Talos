import Foundation

struct MessageEditPlan {
    let prefix: [ChatMessage]
    let user: ChatMessage
    let parentID: UUID?
}

struct MessageRetryPlan {
    let prefix: [ChatMessage]
    let user: ChatMessage
    let assistant: ChatMessage
    let parentID: UUID?
}

enum ConversationBranching {
    static func edit(_ messages: [ChatMessage], messageID: UUID) -> MessageEditPlan? {
        guard let index = messages.firstIndex(where: { $0.id == messageID }),
              messages[index].role == .user else { return nil }
        let prefix = Array(messages[..<index])
        return MessageEditPlan(
            prefix: prefix,
            user: messages[index],
            parentID: prefix.last?.id
        )
    }

    static func retry(_ messages: [ChatMessage], messageID: UUID) -> MessageRetryPlan? {
        guard let index = messages.firstIndex(where: { $0.id == messageID }),
              messages[index].role == .assistant else { return nil }
        guard let userIndex = messages[..<index].lastIndex(where: { $0.role == .user }) else {
            return nil
        }
        let prefix = Array(messages[..<userIndex])
        return MessageRetryPlan(
            prefix: prefix,
            user: messages[userIndex],
            assistant: messages[index],
            parentID: prefix.last?.id
        )
    }

    static func fork(_ messages: [ChatMessage], messageID: UUID) -> [ChatMessage]? {
        guard let index = messages.firstIndex(where: { $0.id == messageID }) else { return nil }
        return Array(messages[...index])
    }
}

import SwiftUI

/// The "Storybook" colours from the design canvas. Every text pair passes WCAG AA.
extension Color {
    static let page = Color(red: 1.0, green: 0.973, blue: 0.925)        // #FFF8EC cream
    static let paper2 = Color(red: 0.965, green: 0.933, blue: 0.867)    // #F6EEDD
    static let card = Color.white
    static let ink = Color(red: 0.122, green: 0.165, blue: 0.267)       // #1F2A44
    static let ink2 = Color(red: 0.353, green: 0.384, blue: 0.459)      // #5A6275
    static let tap = Color(red: 0.184, green: 0.357, blue: 0.827)       // #2F5BD3
    static let highlighter = Color(red: 1.0, green: 0.820, blue: 0.400) // #FFD166
    static let meaning1 = Color(red: 0.055, green: 0.431, blue: 0.400)  // #0E6E66
    static let meaning2 = Color(red: 0.482, green: 0.247, blue: 0.549)  // #7B3F8C
    static let meaning3 = Color(red: 0.604, green: 0.290, blue: 0.071)  // #9A4A12

    /// Each meaning of a word keeps one colour everywhere (its number, its similar words).
    static func meaning(_ index: Int) -> Color {
        [meaning1, meaning2, meaning3][index % 3]
    }
}

extension Font {
    static func rounded(_ size: CGFloat, _ weight: Font.Weight = .heavy) -> Font {
        .system(size: size, weight: weight, design: .rounded)
    }
}

struct CardStyle: ViewModifier {
    var fill: Color = .card
    func body(content: Content) -> some View {
        content
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(fill, in: RoundedRectangle(cornerRadius: 20, style: .continuous))
    }
}

extension View {
    func card(_ fill: Color = .card) -> some View { modifier(CardStyle(fill: fill)) }
}

/// Small uppercase heading used above cards.
struct Overline: View {
    let text: String
    var color: Color = .ink2
    var body: some View {
        Text(text.uppercased())
            .font(.rounded(13, .heavy))
            .tracking(0.8)
            .foregroundStyle(color)
    }
}

/// Simple wrapping row of chips.
struct FlowRow: Layout {
    var spacing: CGFloat = 8
    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let width = proposal.width ?? .infinity
        var x: CGFloat = 0, y: CGFloat = 0, rowHeight: CGFloat = 0, widest: CGFloat = 0
        for view in subviews {
            let size = view.sizeThatFits(.unspecified)
            if x > 0 && x + size.width > width {
                y += rowHeight + spacing
                x = 0
                rowHeight = 0
            }
            x += size.width + spacing
            widest = max(widest, x - spacing)
            rowHeight = max(rowHeight, size.height)
        }
        return CGSize(width: min(widest, width), height: y + rowHeight)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        var x = bounds.minX, y = bounds.minY, rowHeight: CGFloat = 0
        for view in subviews {
            let size = view.sizeThatFits(.unspecified)
            if x > bounds.minX && x + size.width > bounds.maxX {
                y += rowHeight + spacing
                x = bounds.minX
                rowHeight = 0
            }
            view.place(at: CGPoint(x: x, y: y), proposal: ProposedViewSize(size))
            x += size.width + spacing
            rowHeight = max(rowHeight, size.height)
        }
    }
}

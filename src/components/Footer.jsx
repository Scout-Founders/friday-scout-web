export default function Footer({ colors }) {
  return (
    <div style={{ textAlign: "center", padding: 20, borderRadius: 14, background: colors.surfaceLight, border: `1px solid ${colors.border}` }}>
      <p style={{ fontSize: 11, color: colors.textDim, lineHeight: 1.8, margin: 0 }}>
        Scout provides analysis for educational purposes only. This is not financial advice. Scout is not a registered investment adviser (RIA) and does not provide personalized investment advice. All trading involves risk of loss. Past performance does not guarantee future results. You are solely responsible for your trading decisions.
      </p>
    </div>
  );
}

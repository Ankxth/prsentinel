/** The PRSentinel mark: a shield with code brackets around a check mark. */
export default function Logo({ size = 28 }: { size?: number }) {
  return (
    <svg className="logo" width={size} height={size} viewBox="0 0 32 32" role="img" aria-label="PRSentinel logo">
      <defs>
        <linearGradient id="prs-logo-g" x1="4" y1="2" x2="28" y2="30" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#2f81f7" />
          <stop offset="1" stopColor="#a371f7" />
        </linearGradient>
      </defs>
      <path d="M16 2 4 6.8v8.6c0 7.4 4.9 13 12 14.8 7.1-1.8 12-7.4 12-14.8V6.8z" fill="url(#prs-logo-g)" />
      <g fill="none" stroke="#fff" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M10.6 12 7.6 16l3 4" />
        <path d="M21.4 12l3 4-3 4" />
        <path d="m13.4 16.4 2.2 2.3 3.6-4.6" />
      </g>
    </svg>
  );
}

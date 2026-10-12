import type { Metadata, Viewport } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";
import { ThemeProvider } from "@/lib/theme";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "DataOne — AI-First Data Engineering Platform",
  description:
    "Intelligent database engineering, schema mapping, NL-to-SQL, and data governance powered by AI.",
  manifest: "/site.webmanifest",
};

// Next.js 16 moved themeColor out of `metadata` and into its own
// `viewport` export. Hint to the browser which UI scheme to use for
// native controls (form fields, scrollbars).
export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#ffffff" },
    { media: "(prefers-color-scheme: dark)", color: "#09090b" },
  ],
};

// theme_redesign_tasks #1 — hydration-safe theme bootstrap.
// Runs in <head> before paint, so React's first render matches the DOM
// (no flash of wrong theme, no hydration mismatch warning).
const themeBootstrapScript = `
(function () {
  try {
    var t = localStorage.getItem('dp_theme');
    if (t !== 'dark' && t !== 'light') t = 'dark';
    document.documentElement.classList.add(t);
  } catch (e) {
    document.documentElement.classList.add('dark');
  }
})();
`.trim();

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
      // The class is set by the inline script above; React doesn't control
      // it on first render, so suppress the inevitable attribute mismatch.
      suppressHydrationWarning
    >
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeBootstrapScript }} />
      </head>
      <body className="min-h-full flex flex-col bg-background text-fg">
        <ThemeProvider>{children}</ThemeProvider>
      </body>
    </html>
  );
}

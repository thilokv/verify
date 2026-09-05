import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "AI Proptech — verified property search",
  description: "Describe what you want in plain language. Every result carries its title status.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}

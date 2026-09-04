import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Trace",
  description: "Outbound tool. Each campaign keeps its own people, sends, and contact sources.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}

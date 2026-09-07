import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Headway — real-time transit reliability",
  description:
    "Live GTFS-Realtime monitor that detects bus bunching and ghost vehicles as they happen.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}

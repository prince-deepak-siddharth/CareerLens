import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "CareerLens · Career Intelligence",
  description: "Discover opportunities grounded in your skills, experience, and goals.",
};

export default function Layout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}

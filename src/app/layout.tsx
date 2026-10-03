import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'Quiet LA — Tarzana road-noise pilot',
  description: 'Explore modeled exterior road exposure for 80 buildings in Tarzana.',
  robots: { index: false, follow: false, nocache: true },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><head><meta name="robots" content="noindex,nofollow,noarchive" /></head><body>{children}</body></html>;
}

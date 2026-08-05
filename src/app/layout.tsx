import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'Quiet LA — Noise portal',
  description: 'Local Quiet LA modeled and contextual evidence workspace.',
  robots: { index: false, follow: false, nocache: true },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><head><meta name="robots" content="noindex,nofollow,noarchive" /></head><body>{children}</body></html>;
}

import type { Metadata } from 'next';

export const metadata: Metadata = {
  title: 'Quiet LA — How loud is it here?',
  description: 'Modeled road and aircraft noise outside every home in the mapped part of LA County: look up an address and see its day, evening, night and 24 h levels.',
};

export default function MapLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return children;
}

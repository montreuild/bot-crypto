import { redirect } from 'next/navigation';

// S10 — La porte d'entrée devient la page Portefeuille unifiée. On pointe
// directement dessus plutôt que sur /dashboard, pour éviter le saut
// supplémentaire par le 308.
export default function Home() {
  redirect('/portfolio');
}

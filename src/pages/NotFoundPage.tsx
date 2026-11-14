import { ButtonLink } from "@/components/ui/Button";

export function NotFoundPage() {
  return (
    <section className="container" style={{ paddingBlock: "var(--space-16)" }}>
      <h1 style={{ fontSize: "var(--text-xl)" }}>404 — Sayfa bulunamadı</h1>
      <p style={{ margin: "var(--space-4) 0 var(--space-8)", color: "var(--color-text-muted)" }}>
        Aradığın sayfa taşınmış ya da hiç var olmamış olabilir.
      </p>
      <ButtonLink to="/">Ana sayfaya dön</ButtonLink>
    </section>
  );
}

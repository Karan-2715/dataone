import Image from "next/image";

interface BrandProps {
  inverse?: boolean;
  compact?: boolean;
  /** Hide the "DataOne" wordmark, keeping only the Veltris artwork — for icon-rail chrome too narrow for text. */
  showWordmark?: boolean;
}

export function Brand({ inverse = false, compact = false, showWordmark = true }: BrandProps) {
  const width = compact ? 76 : 94;
  const height = compact ? 13 : 16;
  const sizeClass = compact ? "h-[13px] w-[76px] object-contain" : "h-4 w-[94px] object-contain";

  return (
    <span className="inline-flex shrink-0 items-center gap-2.5 whitespace-nowrap">
      {inverse ? (
        // `inverse` pins the white artwork for chrome that's always on a
        // dark surface (e.g. the login page's gradient panel) regardless
        // of the app-wide theme.
        <Image src="/veltris-logo-white.svg" alt="Veltris" width={width} height={height} className={sizeClass} priority />
      ) : (
        // Both variants render unconditionally and `.dark`/`.light` on
        // <html> (set pre-paint, see app/layout.tsx) picks one via CSS —
        // reading theme from React state here would pick the wrong asset
        // during SSR (the server has no access to the visitor's stored
        // theme) and cause a hydration mismatch that never self-corrects.
        <>
          <Image
            src="/veltris-logo.svg"
            alt="Veltris"
            width={width}
            height={height}
            className={`${sizeClass} brand-logo-navy`}
            priority
          />
          <Image
            src="/veltris-logo-white.svg"
            alt="Veltris"
            width={width}
            height={height}
            className={`${sizeClass} brand-logo-white`}
            priority
          />
        </>
      )}
      {showWordmark && (
        <span className={`font-bold tracking-tight ${compact ? "text-lg" : "text-xl"} ${inverse ? "text-white" : "text-fg"}`}>
          DataOne
        </span>
      )}
    </span>
  );
}

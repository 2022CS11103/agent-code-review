import type { Metadata } from "next"
import { IBM_Plex_Mono, IBM_Plex_Sans } from "next/font/google"
import { Providers } from "@/components/providers"
import { Shell } from "@/components/shell"
import "./globals.css"

const sans = IBM_Plex_Sans({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  variable: "--font-ibm",
})

const mono = IBM_Plex_Mono({
  subsets: ["latin"],
  weight: ["400", "500"],
  variable: "--font-ibm-mono",
})

export const metadata: Metadata = {
  title: "PRISM",
  description: "Catch the bug. Prove the fix. Show the evidence.",
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${sans.variable} ${mono.variable} dark h-full antialiased`} suppressHydrationWarning>
      <body className="h-full">
        <script
          dangerouslySetInnerHTML={{
            __html: `try{if(localStorage.getItem("prism-theme")==="light"){document.documentElement.classList.remove("dark")}}catch(e){}`,
          }}
        />
        <Providers>
          <Shell>{children}</Shell>
        </Providers>
      </body>
    </html>
  )
}

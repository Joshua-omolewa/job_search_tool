import "./globals.css";

export const metadata = {
  title: "Job Search Board",
  description: "Local viewer over job_search_pipeline's SQLite store",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}

import { useEffect, useState } from "react";
import Home from "./Home";
import AnalyzeApp from "./AnalyzeApp";

function currentRoute() {
  // Hash routing keeps things dependency-free and works with the static Vite build.
  return window.location.hash.replace(/^#/, "") || "/";
}

export default function App() {
  const [route, setRoute] = useState(currentRoute());

  useEffect(() => {
    const onHashChange = () => setRoute(currentRoute());
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  if (route === "/app" || route === "/analyze") {
    return <AnalyzeApp />;
  }
  return <Home />;
}

import { useEffect } from "react";
import { useAuth } from "./auth";
import { Spinner } from "./components";
import { useRoute } from "./lib";
import Demo from "./screens/Demo";
import DoctorWorklist from "./screens/DoctorWorklist";
import Login from "./screens/Login";
import PathwayView from "./screens/PathwayView";
import PatientHome from "./screens/PatientHome";

// Routes (hash): patient → "#/" · doctor → "#/worklist", "#/p/<pathway id>".
export default function App() {
  const { user, ready } = useAuth();
  const route = useRoute();

  // Patient side uses the calm light theme; clinician side matches the national twin's dark dashboard.
  useEffect(() => {
    if (route[0] === "demo") return;
    if (user) document.documentElement.dataset.mode = user.role;
    const meta = document.querySelector('meta[name="theme-color"]');
    meta?.setAttribute("content", user?.role === "doctor" ? "#070d18" : "#f4f7fa");
  }, [user]);

  if (route[0] === "demo") return <Demo />;   // presenter view; frames log in on their own
  if (!ready) return <div className="center full"><Spinner /></div>;
  if (!user) return <Login />;
  if (user.role === "patient") return <PatientHome />;
  if (route[0] === "p" && route[1]) return <PathwayView id={route[1]} />;
  return <DoctorWorklist />;
}

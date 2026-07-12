import { Routes, Route } from 'react-router-dom'
import { AppLayout } from '@/components/layout/AppLayout'
import { DashboardPage } from '@/pages/Dashboard'
import { IncidentsPage } from '@/pages/IncidentsPage'
import { IncidentDetailPage } from '@/pages/IncidentDetailPage'
import { AgentsPage } from '@/pages/AgentsPage'
import { EvaluationPage } from '@/pages/EvaluationPage'
import { TopologyPage } from '@/pages/TopologyPage'
import { MemoryPage } from '@/pages/MemoryPage'

function App() {
  return (
    <Routes>
      <Route element={<AppLayout />}>
        <Route path="/" element={<DashboardPage />} />
        <Route path="/incidents" element={<IncidentsPage />} />
        <Route path="/incidents/:id" element={<IncidentDetailPage />} />
        <Route path="/agents" element={<AgentsPage />} />
        <Route path="/evaluation" element={<EvaluationPage />} />
        <Route path="/topology" element={<TopologyPage />} />
        <Route path="/memory" element={<MemoryPage />} />
      </Route>
    </Routes>
  )
}

export default App

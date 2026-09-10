import { StockProvider } from '@/state/stock'
import { TopBar } from '@/components/layout/TopBar'
import MarketPage from '@/pages/market'
export default function App() {
  return <StockProvider><div className="flex h-full flex-col bg-futu-bg"><TopBar /><main className="flex min-h-0 flex-1"><MarketPage /></main></div></StockProvider>
}

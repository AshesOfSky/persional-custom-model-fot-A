import MarketPage from '@/pages/market'
import { StockSnapshotProvider, type StockSelection } from '@/state/stock'
export default function TerminalPdfDocument({onReady,selection}:{onReady:()=>void;selection:StockSelection}) {
  return <StockSnapshotProvider selection={selection}><div className="w-[1600px] bg-futu-bg p-4 text-futu-text"><section data-pdf-section="market-overview" className="flex h-[900px]"><MarketPage exportMode onExportSettled={onReady}/></section></div></StockSnapshotProvider>
}

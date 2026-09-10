import path from 'path'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'
export default defineConfig({plugins:[react()],server:{host:'127.0.0.1',port:7100,proxy:{'/core':{target:'http://127.0.0.1:'+(process.env.CUSTOM_MODEL_SHARED_API_PORT||'8010'),changeOrigin:true,rewrite:p=>p.replace(/^\/core/,'')}}},resolve:{alias:{'@':path.resolve(__dirname,'./src')}}})

import { Typography } from 'antd'
import BrandLogo from './BrandLogo'

/** 首页理念条(第三版 spec 的理念文字:潮汐往复，基石恒常。+ 英文原句)。 */
export default function PhilosophyHero() {
  return (
    <div className="philosophy-hero">
      <div style={{ minWidth: 0 }}>
        <Typography.Title level={2} style={{ margin: 0 }}>
          潮汐往复，基石恒常。
        </Typography.Title>
        <Typography.Text type="secondary">
          "Stand firm amid the tides, and do what you truly desire."
        </Typography.Text>
      </div>
      <BrandLogo size={40} />
    </div>
  )
}

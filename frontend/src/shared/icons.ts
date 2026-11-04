/**
 * 고를 수 있는 아이콘 — **이름이 데이터에 저장되고, 그림은 여기서 찾는다.**
 *
 * 타입과 묶음은 `icon` 에 **이름**(`Wrench`)을 담는다. 그림 자체를 담을 수 없기
 * 때문이고(JSON 에 SVG 를 넣으면 정의가 읽을 수 없게 커진다), 그래서 이름을
 * 그림으로 바꾸는 자리가 하나 필요하다. 여기다.
 *
 * ## 왜 고르게 하나
 *
 * 사이드바에서 타입이 열둘쯤 되면 **전부 같은 네모**여서 이름을 한 자씩 읽어야
 * 한다. 눈은 모양을 먼저 잡는데 모양이 하나뿐이면 그 능력이 통째로 안 쓰인다.
 *
 * ## 왜 전부(수천 개)를 안 열어 두나
 *
 * lucide 는 3,600개를 준다. 전부 고르게 하면 (1) 고르는 일이 검색이 아니라 목록이
 * 되고, (2) 설치마다 제각각인 그림이 서고, (3) 번들에 3,600개가 딸려 온다. 여기
 * 골라 둔 것만 **정적으로 import** 해서 그 셋을 다 막는다.
 *
 * 모르는 이름이 오면 기본 그림으로 떨어진다 — **메뉴가 통째로 안 뜨는 것보다 낫다.**
 * 다른 설치에서 가져온 정의에 여기 없는 이름이 들어 있을 수 있다.
 */

import {
  Atom,
  Award,
  Banknote,
  Beaker,
  Bell,
  BookOpen,
  Box,
  Boxes,
  Briefcase,
  Building2,
  Calendar,
  CalendarClock,
  Car,
  CircuitBoard,
  ClipboardCheck,
  ClipboardList,
  Clock,
  Cog,
  Component,
  Cpu,
  Database,
  Dna,
  Factory,
  FileText,
  FlaskConical,
  FolderOpen,
  Fuel,
  Gauge,
  GitBranch,
  Hammer,
  HardHat,
  Key,
  Layers,
  LayoutGrid,
  Leaf,
  Lightbulb,
  MapPin,
  Microscope,
  Milestone,
  Network,
  Package,
  Plug,
  Puzzle,
  Recycle,
  Route,
  Ruler,
  Scale,
  Server,
  Shapes,
  Shield,
  ShoppingCart,
  Sigma,
  Star,
  Tag,
  Target,
  TestTube,
  Thermometer,
  Truck,
  Users,
  Warehouse,
  Workflow,
  Wrench,
  Zap,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'

export interface IconChoice {
  name: string
  Icon: LucideIcon
  /** 고르는 사람이 찾을 말. **한글로 찾는다** — 아이콘 이름은 영어라 못 찾는다. */
  label: string
  /** 같은 그림을 다른 말로 찾는 사람을 위해. 화면에는 안 보인다. */
  keywords?: string
}

/** 무리. 고르개가 이 차례로 세운다 — 훑는 사람이 「이 근처」 를 짐작할 수 있게. */
export const ICON_GROUPS = [
  '물건',
  '생성',
  '재고·물류',
  '조직·사람',
  '문서·일정',
  '그 밖에',
] as const

export type IconGroup = (typeof ICON_GROUPS)[number]

export const ICON_CATALOG: Record<IconGroup, IconChoice[]> = {
  물건: [
    { name: 'Box', Icon: Box, label: '상자', keywords: '부품 품목 아이템' },
    { name: 'Boxes', Icon: Boxes, label: '상자 여럿', keywords: '품목군 모음' },
    { name: 'Component', Icon: Component, label: '구성품', keywords: '컴포넌트 조립' },
    { name: 'Puzzle', Icon: Puzzle, label: '조각', keywords: '모듈 부분' },
    { name: 'Shapes', Icon: Shapes, label: '도형', keywords: '형상 종류' },
    { name: 'Layers', Icon: Layers, label: '겹', keywords: '층 레이어 적층' },
    { name: 'Package', Icon: Package, label: '포장', keywords: '제품 출하' },
    { name: 'Car', Icon: Car, label: '차량', keywords: '자동차 완성차' },
  ],
  생성: [
    { name: 'Wrench', Icon: Wrench, label: '공구', keywords: '정비 수리 도구' },
    { name: 'Hammer', Icon: Hammer, label: '망치', keywords: '작업 제작' },
    { name: 'Cog', Icon: Cog, label: '톱니', keywords: '기계 설비 장비' },
    { name: 'Factory', Icon: Factory, label: '공장', keywords: '생산 라인 공정' },
    { name: 'HardHat', Icon: HardHat, label: '안전모', keywords: '현장 작업자 안전' },
    { name: 'Gauge', Icon: Gauge, label: '계기', keywords: '측정 성능 지표' },
    { name: 'Ruler', Icon: Ruler, label: '자', keywords: '치수 측정 공차' },
    { name: 'Thermometer', Icon: Thermometer, label: '온도계', keywords: '온도 환경' },
    { name: 'FlaskConical', Icon: FlaskConical, label: '플라스크', keywords: '시험 실험 화학' },
    { name: 'Beaker', Icon: Beaker, label: '비커', keywords: '시험 재료' },
    { name: 'TestTube', Icon: TestTube, label: '시험관', keywords: '시료 샘플' },
    { name: 'Microscope', Icon: Microscope, label: '현미경', keywords: '분석 관찰 해석' },
    { name: 'Atom', Icon: Atom, label: '원자', keywords: '해석 물성 재료' },
    { name: 'Dna', Icon: Dna, label: '유전자', keywords: '계보 이력 구조' },
    { name: 'Cpu', Icon: Cpu, label: '칩', keywords: '전자 제어기 ECU' },
    { name: 'CircuitBoard', Icon: CircuitBoard, label: '회로', keywords: '전장 기판' },
    { name: 'Plug', Icon: Plug, label: '플러그', keywords: '전원 연결' },
    { name: 'Zap', Icon: Zap, label: '번개', keywords: '전기 전력' },
    { name: 'Fuel', Icon: Fuel, label: '연료', keywords: '주유 에너지' },
    { name: 'Leaf', Icon: Leaf, label: '잎', keywords: '환경 친환경 탄소' },
    { name: 'Recycle', Icon: Recycle, label: '재활용', keywords: '순환 폐기' },
  ],
  '재고·물류': [
    { name: 'Warehouse', Icon: Warehouse, label: '창고', keywords: '재고 보관' },
    { name: 'Truck', Icon: Truck, label: '트럭', keywords: '배송 물류 납품' },
    { name: 'Route', Icon: Route, label: '경로', keywords: '공정 흐름 이동' },
    { name: 'ShoppingCart', Icon: ShoppingCart, label: '장바구니', keywords: '구매 발주' },
    { name: 'Banknote', Icon: Banknote, label: '지폐', keywords: '비용 원가 금액' },
    { name: 'Scale', Icon: Scale, label: '저울', keywords: '무게 균형 비교' },
    { name: 'Tag', Icon: Tag, label: '꼬리표', keywords: '분류 라벨 코드' },
  ],
  '조직·사람': [
    { name: 'Building2', Icon: Building2, label: '건물', keywords: '부서 조직 회사' },
    { name: 'Users', Icon: Users, label: '사람들', keywords: '조직 팀 멤버' },
    { name: 'Briefcase', Icon: Briefcase, label: '가방', keywords: '업무 프로젝트 과제' },
    { name: 'Shield', Icon: Shield, label: '방패', keywords: '품질 보증 안전' },
    { name: 'Award', Icon: Award, label: '상', keywords: '인증 등급 평가' },
    { name: 'Star', Icon: Star, label: '별', keywords: '중요 등급 즐겨찾기' },
    { name: 'Key', Icon: Key, label: '열쇠', keywords: '권한 기준 라이선스' },
  ],
  '문서·일정': [
    { name: 'FileText', Icon: FileText, label: '문서', keywords: '보고서 사양서' },
    { name: 'FolderOpen', Icon: FolderOpen, label: '폴더', keywords: '모음 분류' },
    { name: 'BookOpen', Icon: BookOpen, label: '책', keywords: '표준 규격 지침' },
    { name: 'ClipboardList', Icon: ClipboardList, label: '점검표', keywords: '목록 항목' },
    { name: 'ClipboardCheck', Icon: ClipboardCheck, label: '확인표', keywords: '검사 승인 판정' },
    { name: 'Calendar', Icon: Calendar, label: '달력', keywords: '일정 날짜' },
    { name: 'CalendarClock', Icon: CalendarClock, label: '기한', keywords: '마감 일정' },
    { name: 'Clock', Icon: Clock, label: '시계', keywords: '시간 이력' },
    { name: 'Milestone', Icon: Milestone, label: '이정표', keywords: '단계 마일스톤' },
    { name: 'Target', Icon: Target, label: '과녁', keywords: '목표 지표 KPI' },
  ],
  '그 밖에': [
    { name: 'LayoutGrid', Icon: LayoutGrid, label: '격자', keywords: '기본 일반' },
    { name: 'Database', Icon: Database, label: '데이터', keywords: '기준정보 마스터' },
    { name: 'Server', Icon: Server, label: '서버', keywords: '시스템 설비' },
    { name: 'Network', Icon: Network, label: '망', keywords: '연결 관계 네트워크' },
    { name: 'Workflow', Icon: Workflow, label: '흐름', keywords: '공정 절차 프로세스' },
    { name: 'GitBranch', Icon: GitBranch, label: '갈래', keywords: '버전 형상 파생' },
    { name: 'Sigma', Icon: Sigma, label: '시그마', keywords: '통계 합계 지표' },
    { name: 'MapPin', Icon: MapPin, label: '핀', keywords: '위치 장소 거점' },
    { name: 'Lightbulb', Icon: Lightbulb, label: '전구', keywords: '아이디어 제안' },
    { name: 'Bell', Icon: Bell, label: '종', keywords: '알림 경고' },
  ],
}

/** 이름 → 그림. 한 번만 만든다. */
const BY_NAME: Record<string, LucideIcon> = Object.fromEntries(
  Object.values(ICON_CATALOG).flatMap((list) => list.map((one) => [one.name, one.Icon])),
)

export const DEFAULT_ICON = LayoutGrid

/**
 * 이름으로 그림을 찾는다. **모르는 이름이면 기본으로 떨어진다.**
 *
 * 던지지 않는 이유: 이 값은 데이터에서 오고, 다른 설치에서 가져온 정의에 여기 없는
 * 이름이 들어 있을 수 있다. 그때 메뉴가 통째로 안 뜨는 것이 훨씬 나쁘다.
 */
export function iconOf(name: string | null | undefined): LucideIcon {
  return (name && BY_NAME[name]) || DEFAULT_ICON
}

/** 고르개가 훑는 전체 목록 — 무리 차례 그대로. */
export function allIcons(): IconChoice[] {
  return ICON_GROUPS.flatMap((group) => ICON_CATALOG[group])
}

/** 찾는 말에 걸리나. 한글 이름·영어 이름·별말 전부 본다. */
export function matches(choice: IconChoice, query: string): boolean {
  const needle = query.trim().toLowerCase()
  if (!needle) return true
  return `${choice.label} ${choice.name} ${choice.keywords ?? ''}`.toLowerCase().includes(needle)
}

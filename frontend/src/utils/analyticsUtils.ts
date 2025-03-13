import { AnalyticsRequest, AnalyticsResult } from '../api/models'
import { performAnalytics } from '../api/api'

const STATE_ANALYTICS_REGEX = /which state|state with|by state|states with|states that/i
const PROVIDER_ANALYTICS_REGEX = /which provider|provider with|by provider|providers with|providers that/i
const INSURANCE_ANALYTICS_REGEX = /which insurance|insurance with|by insurance|insurances with|insurance companies/i

const MOST_REGEX = /most|highest|largest|greatest|maximum/i
const LEAST_REGEX = /least|lowest|smallest|minimum/i
const AVERAGE_REGEX = /average|avg|mean/i
const TOTAL_REGEX = /total|sum|add/i

const CLAIMS_REGEX = /claims|claim amount/i
const CHARGES_REGEX = /charges|line item/i
const PAYMENTS_REGEX = /payments|paid|amount paid/i
const BALANCE_REGEX = /balance|due|owed/i
const COUNT_REGEX = /number of|count of|how many/i

export function detectAnalyticsQuery(query: string): AnalyticsRequest | null {
    let groupByField: string | null = null
    
    if (STATE_ANALYTICS_REGEX.test(query)) {
        groupByField = 'patientState'
    } else if (PROVIDER_ANALYTICS_REGEX.test(query)) {
        groupByField = 'providerName'
    } else if (INSURANCE_ANALYTICS_REGEX.test(query)) {
        groupByField = 'insuranceCompany'
    } else {
        return null
    }

    let metricFunction: string = 'count'
    let metricField: string | null = null
    
    if (MOST_REGEX.test(query) || LEAST_REGEX.test(query)) {
        if (COUNT_REGEX.test(query)) {
            metricFunction = 'count'
        } else if (AVERAGE_REGEX.test(query)) {
            metricFunction = 'avg'
            if (CLAIMS_REGEX.test(query)) {
                metricField = 'claimAmount'
            } else if (CHARGES_REGEX.test(query)) {
                metricField = 'lineItemCharge'
            } else if (PAYMENTS_REGEX.test(query)) {
                metricField = 'amountPaid'
            } else if (BALANCE_REGEX.test(query)) {
                metricField = 'balanceDue'
            } else {
                metricField = 'claimAmount'
            }
        } else if (TOTAL_REGEX.test(query)) {
            metricFunction = 'sum'
            if (CLAIMS_REGEX.test(query)) {
                metricField = 'claimAmount'
            } else if (CHARGES_REGEX.test(query)) {
                metricField = 'lineItemCharge'
            } else if (PAYMENTS_REGEX.test(query)) {
                metricField = 'amountPaid'
            } else if (BALANCE_REGEX.test(query)) {
                metricField = 'balanceDue'
            } else {
                metricField = 'claimAmount'
            }
        } else {
            metricFunction = 'count'
        }
    }

    const order = LEAST_REGEX.test(query) ? 'asc' : 'desc'

    const request: AnalyticsRequest = {
        group_by_field: groupByField,
        metric_function: metricFunction as any,
        order: order as 'asc' | 'desc',
        top_results: 10
    }

    if (metricField && metricFunction !== 'count') {
        request.metric_field = metricField
    }
    
    return request
}

export async function handleAnalyticsQuery(queryText: string): Promise<AnalyticsResult | null> {
    const analyticsRequest = detectAnalyticsQuery(queryText)
    
    if (!analyticsRequest) {
        return null
    }
    
    return await performAnalytics(analyticsRequest)
}

export function formatAnalyticsResult(result: AnalyticsResult | null, queryText: string): string {
    if (!result || result.error || !result.results || result.results.length === 0) {
        return `I couldn't analyze that data. ${result?.error || 'Please try a different query.'}`
    }
    
    const { results, group_by_field, metric_function, metric_field } = result

    let response = ''

    const groupFieldReadable = group_by_field.replace(/([A-Z])/g, ' $1').toLowerCase().trim()

    if (metric_function === 'count' || !metric_field) {
        const topResult = results[0]
        response = `The ${groupFieldReadable} with the most claims is ${topResult.group} with ${topResult.count} claims.`

        if (results.length > 1) {
            response += '\n\nHere is the breakdown of the top results:'
            
            results.forEach((result, index) => {
                response += `\n${index + 1}. ${result.group}: ${result.count} claims`
            })
        }
    }
    else {
        const metricFieldReadable = metric_field.replace(/([A-Z])/g, ' $1').toLowerCase().trim()

        let metricFunctionReadable = ''
        switch (metric_function) {
            case 'avg':
                metricFunctionReadable = 'average'
                break
            case 'sum':
                metricFunctionReadable = 'total'
                break
            case 'min':
                metricFunctionReadable = 'minimum'
                break
            case 'max':
                metricFunctionReadable = 'maximum'
                break
            default:
                metricFunctionReadable = metric_function
        }

        const topResult = results[0]
        const formattedMetric = typeof topResult.metric === 'number' ? 
            `$${topResult.metric.toFixed(2)}` : 
            topResult.metric
            
        response = `The ${groupFieldReadable} with the ${metricFunctionReadable} ${metricFieldReadable} is ${topResult.group} with ${formattedMetric}.`

        if (results.length > 1) {
            response += '\n\nHere is the breakdown of the top results:'
            
            results.forEach((result, index) => {
                const formattedMetric = typeof result.metric === 'number' ? 
                    `$${result.metric.toFixed(2)}` : 
                    result.metric
                    
                response += `\n${index + 1}. ${result.group}: ${formattedMetric}`
            })
        }
    }
    return response
}